"""Operaciones sobre el grafo de un caso: subgrafos, caminos, fusión y revisión. Sin FastAPI."""

from dataclasses import dataclass, field

import networkx as nx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aleph.core.models import Account, Entity, Relation

from .. import auditlog
from ..errors import Conflict, Invalid, NotFound
from .util import get_in_case

STATUSES = ("proposed", "confirmed", "rejected")
VISIBLE = ("proposed", "confirmed")  # por defecto lo rechazado no se dibuja


@dataclass
class Subgraph:
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    truncated: bool = False

    def degrees(self) -> dict[int, int]:
        degree = {e.id: 0 for e in self.entities}
        for r in self.relations:
            degree[r.src_id] += 1
            degree[r.dst_id] += 1
        return degree


def _relations_between(session: Session, case_id: int, ids: set[int], statuses) -> list[Relation]:
    if not ids:
        return []
    rows = session.execute(
        select(Relation).where(Relation.case_id == case_id, Relation.status.in_(statuses)).order_by(Relation.id)
    ).scalars()
    return [r for r in rows if r.src_id in ids and r.dst_id in ids]


def case_graph(
    session: Session,
    case_id: int,
    *,
    types: list[str] | None = None,
    statuses: tuple[str, ...] | list[str] = VISIBLE,
    min_confidence: float = 0.0,
    include_isolated: bool = True,
    limit: int | None = None,
) -> Subgraph:
    stmt = select(Entity).where(Entity.case_id == case_id, Entity.status.in_(statuses)).order_by(Entity.id)
    if types:
        stmt = stmt.where(Entity.type.in_(types))
    if min_confidence > 0:
        stmt = stmt.where(Entity.confidence >= min_confidence)
    truncated = False
    if limit is not None:
        entities = session.execute(stmt.limit(limit + 1)).scalars().all()
        truncated = len(entities) > limit
        entities = entities[:limit]
    else:
        entities = session.execute(stmt).scalars().all()
    relations = [
        r for r in _relations_between(session, case_id, {e.id for e in entities}, statuses)
        if r.confidence >= min_confidence
    ]
    if not include_isolated:
        connected = {r.src_id for r in relations} | {r.dst_id for r in relations}
        entities = [e for e in entities if e.id in connected]
    return Subgraph(entities=list(entities), relations=relations, truncated=truncated)


def _nx_graph(session: Session, case_id: int, statuses) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(
        session.execute(
            select(Entity.id).where(Entity.case_id == case_id, Entity.status.in_(statuses))
        ).scalars()
    )
    for src, dst in session.execute(
        select(Relation.src_id, Relation.dst_id).where(Relation.case_id == case_id, Relation.status.in_(statuses))
    ):
        if src in graph and dst in graph:
            graph.add_edge(src, dst)
    return graph


def _materialize(session: Session, case_id: int, ids: list[int], statuses) -> Subgraph:
    if not ids:
        return Subgraph()
    by_id = {e.id: e for e in session.execute(select(Entity).where(Entity.id.in_(ids))).scalars()}
    entities = [by_id[i] for i in ids if i in by_id]
    return Subgraph(entities=entities, relations=_relations_between(session, case_id, set(ids), statuses))


def neighborhood(
    session: Session, case_id: int, entity_id: int, *, depth: int = 1, statuses=VISIBLE
) -> Subgraph:
    """El nodo y todo lo que está a `depth` saltos o menos."""
    entity = get_in_case(session, Entity, case_id, entity_id, "La entidad")
    graph = _nx_graph(session, case_id, statuses)
    if entity.id not in graph:  # p. ej. una entidad rechazada: se muestra sola
        return Subgraph(entities=[entity])
    distances = nx.single_source_shortest_path_length(graph, entity.id, cutoff=depth)
    ids = sorted(distances, key=lambda i: (distances[i], i))
    return _materialize(session, case_id, ids, statuses)


def shortest_path(
    session: Session, case_id: int, source_id: int, target_id: int, *, statuses=VISIBLE
) -> Subgraph | None:
    """Camino más corto (sin dirección) entre dos entidades del caso, o None si no están conectadas."""
    get_in_case(session, Entity, case_id, source_id, "La entidad")
    get_in_case(session, Entity, case_id, target_id, "La entidad")
    graph = _nx_graph(session, case_id, statuses)
    try:
        ids = nx.shortest_path(graph, source_id, target_id)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    sub = _materialize(session, case_id, ids, statuses)
    steps = {frozenset(pair) for pair in zip(ids, ids[1:], strict=False)}
    sub.relations = [r for r in sub.relations if frozenset((r.src_id, r.dst_id)) in steps]
    return sub


@dataclass
class MergeResult:
    entity: Entity
    relations_repointed: int = 0
    relations_removed: int = 0
    accounts_repointed: int = 0


def merge_entities(
    session: Session, case_id: int, keep_id: int, duplicate_id: int, user_id: int | None = None
) -> MergeResult:
    """Fusiona `duplicate_id` dentro de `keep_id`: reapunta relaciones y cuentas y borra el duplicado.

    Las relaciones que quedarían como lazo (a -> a) o repetidas (mismo origen, destino y tipo)
    se eliminan, conservando la de mayor confianza. No hace commit.
    """
    if keep_id == duplicate_id:
        raise Invalid("No se puede fusionar una entidad consigo misma.")
    auditlog.guard(session)
    keep = get_in_case(session, Entity, case_id, keep_id, "La entidad")
    dup = get_in_case(session, Entity, case_id, duplicate_id, "La entidad")
    if keep.type != dup.type:
        raise Invalid(f"Solo se fusionan entidades del mismo tipo ({keep.type} ≠ {dup.type}).")
    keep_accounts = session.execute(select(Account).where(Account.entity_id == keep.id)).scalars().all()
    dup_accounts = session.execute(select(Account).where(Account.entity_id == dup.id)).scalars().all()
    if keep_accounts and dup_accounts:
        raise Conflict(
            "Ambas entidades representan cuentas recolectadas distintas: no se fusionan. "
            "Para vincularlas usá la revisión de MENARD."
        )

    result = MergeResult(entity=keep)
    for account in dup_accounts:
        account.entity_id = keep.id
        result.accounts_repointed += 1

    touching = session.execute(
        select(Relation).where(
            Relation.case_id == case_id, or_(Relation.src_id == dup.id, Relation.dst_id == dup.id)
        ).order_by(Relation.id)
    ).scalars().all()
    existing = {
        (r.src_id, r.dst_id, r.type): r
        for r in session.execute(
            select(Relation).where(
                Relation.case_id == case_id, or_(Relation.src_id == keep.id, Relation.dst_id == keep.id)
            ).order_by(Relation.id)
        ).scalars()
        if r.src_id != dup.id and r.dst_id != dup.id
    }
    for rel in touching:
        src = keep.id if rel.src_id == dup.id else rel.src_id
        dst = keep.id if rel.dst_id == dup.id else rel.dst_id
        if src == dst:
            session.delete(rel)
            result.relations_removed += 1
            continue
        twin = existing.get((src, dst, rel.type))
        if twin is not None:
            twin.confidence = max(twin.confidence, rel.confidence)
            if rel.status == "confirmed":
                twin.status = "confirmed"
            twin.props = {**(rel.props or {}), **(twin.props or {})}
            session.delete(rel)
            result.relations_removed += 1
            continue
        rel.src_id, rel.dst_id = src, dst
        existing[(src, dst, rel.type)] = rel
        result.relations_repointed += 1

    props = {**(dup.props or {}), **(keep.props or {})}
    merged_from = list((keep.props or {}).get("merged_from", []))
    merged_from.append({"id": dup.id, "label": dup.label, "source_id": dup.source_id})
    merged_from.extend((dup.props or {}).get("merged_from", []))
    props["merged_from"] = merged_from
    keep.props = props
    if dup.status == "confirmed" and keep.status == "proposed":
        keep.status = "confirmed"
    if keep.source_id is None:
        keep.source_id = dup.source_id
    dup_label, dup_source = dup.label, dup.source_id
    session.flush()
    session.delete(dup)
    session.flush()
    auditlog.record(
        session, "entity.merge", user_id=user_id, case_id=case_id, target=f"entity:{keep.id}",
        detail={
            "kept": keep.id, "merged": duplicate_id, "merged_label": dup_label, "merged_source_id": dup_source,
            "relations_repointed": result.relations_repointed, "relations_removed": result.relations_removed,
            "accounts_repointed": result.accounts_repointed,
        },
    )
    return result


def review_item(
    session: Session, case_id: int, model, obj_id: int, decision: str, user_id: int, note: str = ""
):
    """Acepta o rechaza una entidad o relación propuesta (por FUNES u otro motor). No hace commit.

    Rechazar una entidad rechaza también las relaciones propuestas que la tocan.
    Aceptar una relación exige que sus dos extremos no estén rechazados.
    """
    if decision not in ("accept", "reject"):
        raise Invalid("La decisión debe ser 'accept' o 'reject'.")
    is_entity = model is Entity
    what = "La entidad" if is_entity else "La relación"
    auditlog.guard(session)
    obj = get_in_case(session, model, case_id, obj_id, what)
    if obj.status != "proposed":
        raise Conflict(f"{what} no está en estado propuesto (estado actual: {obj.status}).")
    cascaded: list[int] = []
    if decision == "accept":
        if not is_entity:
            ends = [session.get(Entity, obj.src_id), session.get(Entity, obj.dst_id)]
            if any(e is None or e.status == "rejected" for e in ends):
                raise Conflict("No se puede aceptar una relación con un extremo rechazado.")
        obj.status = "confirmed"
    else:
        obj.status = "rejected"
        if is_entity:
            for rel in session.execute(
                select(Relation).where(
                    Relation.case_id == case_id, Relation.status == "proposed",
                    or_(Relation.src_id == obj.id, Relation.dst_id == obj.id),
                )
            ).scalars():
                rel.status = "rejected"
                cascaded.append(rel.id)
    session.flush()
    kind = "entity" if is_entity else "relation"
    auditlog.record(
        session, f"{kind}.review", user_id=user_id, case_id=case_id, target=f"{kind}:{obj.id}",
        detail={"decision": decision, "note": note, "relations_rejected": cascaded},
    )
    return obj


def delete_entity(session: Session, case_id: int, entity_id: int, user_id: int | None = None) -> int:
    """Borra una entidad y sus relaciones. Devuelve cuántas relaciones se borraron. No hace commit."""
    auditlog.guard(session)
    entity = get_in_case(session, Entity, case_id, entity_id, "La entidad")
    if session.execute(select(Account.id).where(Account.entity_id == entity.id)).first() is not None:
        raise Conflict("La entidad representa una cuenta recolectada y no se puede borrar.")
    relations = session.execute(
        select(Relation).where(or_(Relation.src_id == entity.id, Relation.dst_id == entity.id))
    ).scalars().all()
    for rel in relations:
        session.delete(rel)
    session.flush()
    detail = {"type": entity.type, "label": entity.label, "relations_deleted": [r.id for r in relations]}
    session.delete(entity)
    session.flush()
    auditlog.record(session, "entity.delete", user_id=user_id, case_id=case_id,
                    target=f"entity:{entity_id}", detail=detail)
    return len(relations)


__all__ = [
    "STATUSES",
    "VISIBLE",
    "MergeResult",
    "NotFound",
    "Subgraph",
    "case_graph",
    "delete_entity",
    "merge_entities",
    "neighborhood",
    "review_item",
    "shortest_path",
]
