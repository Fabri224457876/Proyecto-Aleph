"""Persistencia de propuestas: entidades y relaciones que llegan de un motor o de una herramienta.

Todo lo que entra desde fuera queda como `proposed` (salvo que el llamador pida otro estado) y
cuelga de una `Source` con su crudo en disco y su sha256. Un ítem que ya existe en el caso (mismo
tipo y etiqueta, sin distinguir mayúsculas) se reutiliza y conserva su estado: lo que un analista
rechazó no vuelve a aparecer como nuevo. No hace commit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aleph.core.models import Entity, Job, Relation, Source
from aleph.core.schemas import ENTITY_TYPES, EntityRecord, RelationRecord

from .util import clamp01

LABEL_MAX = 500
REL_TYPE_MAX = 64
CONNECTOR_MAX = 64


class PersistSummary(BaseModel):
    entities_created: int = 0
    entities_existing: int = 0
    relations_created: int = 0
    relations_existing: int = 0
    entity_ids: list[int] = Field(default_factory=list, description="Entidades del lote, nuevas o reutilizadas")
    relation_ids: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def clean_props(props: dict[str, Any] | None) -> dict[str, Any]:
    """Props como JSON puro: lo que no se serializa (fechas, tuplas) queda como texto."""
    return json.loads(json.dumps(props or {}, default=str, ensure_ascii=False))


def store_raw(data_dir: str | Path, case_id: int, payload: bytes, suffix: str) -> tuple[str, str]:
    """Guarda el crudo en `cases/<id>/raw/<sha256><sufijo>`. Devuelve (sha256, ruta relativa)."""
    sha = hashlib.sha256(payload).hexdigest()
    rel = Path("cases") / str(case_id) / "raw" / f"{sha}{suffix}"
    target = Path(data_dir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(payload)
    return sha, rel.as_posix()


def make_source(
    session: Session,
    case_id: int,
    user_id: int | None,
    *,
    kind: str,
    connector: str,
    reference: str,
    payload: bytes | None,
    data_dir: str | Path,
    suffix: str = ".json",
) -> Source:
    """Procedencia de una propuesta. Si hay `payload`, lo guarda como crudo con su hash."""
    sha, raw_path = ("", "")
    if payload is not None:
        sha, raw_path = store_raw(data_dir, case_id, payload, suffix)
    source = Source(
        case_id=case_id, kind=kind, connector=connector[:CONNECTOR_MAX], reference=reference[:2000],
        sha256=sha, raw_path=raw_path, created_by=user_id, retrieved_at=datetime.now(UTC),
    )
    session.add(source)
    session.flush()
    return source


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")


def persist_graph(
    session: Session,
    case_id: int,
    entities: list[EntityRecord],
    relations: list[RelationRecord],
    *,
    source_id: int | None,
    status: str = "proposed",
) -> tuple[PersistSummary, dict[str, int]]:
    """Guarda un lote de entidades y relaciones. Devuelve el resumen y el mapa ref -> id de entidad.

    Las relaciones que referencian una entidad que no llegó en el lote, o que son lazos, se omiten
    con un aviso. No registra auditoría: lo hace quien llama, con el detalle que corresponde.
    """
    summary = PersistSummary()
    ids: dict[str, int] = {}
    seen_entities: set[int] = set()
    for rec in entities:
        label = (rec.label or "").strip()[:LABEL_MAX]
        if rec.type not in ENTITY_TYPES or not label:
            summary.warnings.append(f"Entidad descartada ({rec.type!r}, {rec.label!r}): tipo o etiqueta inválidos.")
            continue
        found = session.execute(
            select(Entity).where(
                Entity.case_id == case_id, Entity.type == rec.type, func.lower(Entity.label) == label.lower(),
            ).order_by(Entity.id)
        ).scalars().first()
        if found is None:
            found = Entity(
                case_id=case_id, type=rec.type, label=label, props=clean_props(rec.props),
                confidence=clamp01(rec.confidence), status=status, source_id=source_id,
            )
            session.add(found)
            session.flush()
            summary.entities_created += 1
        else:
            summary.entities_existing += 1
        if rec.ref:
            ids[rec.ref] = found.id
        ids.setdefault(f"{rec.type}:{label}", found.id)
        if found.id not in seen_entities:
            seen_entities.add(found.id)
            summary.entity_ids.append(found.id)

    seen_relations: set[int] = set()
    for rec in relations:
        src, dst = ids.get(rec.src_ref), ids.get(rec.dst_ref)
        rel_type = rec.type.strip()[:REL_TYPE_MAX]
        if not rel_type or src is None or dst is None or src == dst:
            summary.warnings.append(f"Relación {rec.src_ref!r} -[{rec.type}]-> {rec.dst_ref!r} omitida.")
            continue
        found = session.execute(
            select(Relation).where(
                Relation.case_id == case_id, Relation.src_id == src, Relation.dst_id == dst,
                Relation.type == rel_type,
            ).order_by(Relation.id)
        ).scalars().first()
        if found is None:
            found = Relation(
                case_id=case_id, src_id=src, dst_id=dst, type=rel_type, props=clean_props(rec.props),
                confidence=clamp01(rec.confidence), status=status, source_id=source_id,
            )
            session.add(found)
            session.flush()
            summary.relations_created += 1
        else:
            summary.relations_existing += 1
        if found.id not in seen_relations:
            seen_relations.add(found.id)
            summary.relation_ids.append(found.id)
    return summary, ids


def record_job(
    session: Session,
    case_id: int,
    kind: str,
    *,
    params: dict[str, Any],
    result: dict[str, Any],
    user_id: int | None,
    status: str = "done",
    error: str = "",
) -> Job:
    """Deja constancia de una ejecución en la tabla de trabajos (las de la API son síncronas)."""
    job = Job(
        case_id=case_id, kind=kind, status=status, params=clean_props(params), result=clean_props(result),
        error=error, created_by=user_id, finished_at=datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    return job
