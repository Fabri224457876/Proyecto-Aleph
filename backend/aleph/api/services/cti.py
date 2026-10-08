"""Integración CTI: IOCs y enriquecimiento como propuestas, exportación e importación STIX/MISP,
técnicas ATT&CK vinculadas al caso y datachunks detectados en un texto (sin LLM).

Reglas que este módulo hace cumplir:
- Lo que sale de un motor o de un proveedor entra como `proposed`, con su `Source`.
- En una exportación solo van entidades, relaciones y técnicas confirmadas, salvo que se pida
  explícitamente incluir las pendientes. Las hipótesis de MENARD pendientes (`AccountLink` en
  `pending`) solo se exportan con esa opción, marcadas como pendientes.
- Un vínculo ATT&CK tiene `estado`: "confirmada" (la puso un analista) o "propuesta" (llegó con una
  importación). Solo las confirmadas entran a la capa de Navigator salvo que se pida lo contrario.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aleph.core.models import (
    Account,
    AccountLink,
    Case,
    CaseSection,
    Entity,
    Finding,
    Relation,
    Source,
)
from aleph.core.schemas import Datachunk, EntityRecord, RelationRecord
from aleph.cti.attack import AttackIndex, Technique, load_index, navigator_layer
from aleph.cti.enrich.base import EnrichmentProvider, indicator_value
from aleph.cti.enrich.orchestrator import DEFAULT_PROVIDERS, EnrichmentReport, enrich_indicator
from aleph.cti.ioc import extract_iocs
from aleph.cti.misp import MispExport, export_misp
from aleph.cti.models import TECHNIQUE_ID_RE, CtiCase, TechniqueScore
from aleph.cti.stix import StixExport, export_case, import_bundle
from aleph.cti.tlp import TlpLevel
from aleph.funes.ar_rules import find_rule_hits

from ..errors import Invalid
from .lens import ensure_default_sections
from .proposals import canonical_json, clean_props, make_source, persist_graph
from .util import as_utc

ATTACK_SECTION = "Actividad"
STATUS_CONFIRMED = "confirmada"
STATUS_PROPOSED = "propuesta"
SAME_OPERATOR = "same_operator"
PENDING_HYPOTHESIS = "Hipótesis de mismo operador (MENARD) pendiente de revisión humana."
MAX_CHUNKS = 500


# ---------------------------------------------------------------- IOCs


def extract_iocs_to_graph(session: Session, case: Case, text: str, user_id: int | None, data_dir) -> dict[str, Any]:
    """Extrae IOCs del texto y los guarda como propuestas. Las técnicas ATT&CK mencionadas no se
    vinculan solas: se devuelven como sugerencia para que el analista decida."""
    extraction = extract_iocs(text)
    source = make_source(
        session, case.id, user_id, kind="manual", connector="ioc-extractor",
        reference="texto pegado por el analista (extracción de IOCs)", payload=text.encode("utf-8"),
        data_dir=data_dir, suffix=".txt",
    )
    summary, _ = persist_graph(session, case.id, extraction.entities, [], source_id=source.id)
    return {
        "source_id": source.id,
        "summary": summary,
        "techniques": extraction.techniques,
        "discarded": extraction.discarded,
        "defanged_input": extraction.defanged_input,
    }


# ---------------------------------------------------------------- enriquecimiento


def applicable_providers(entity_type: str, injected: list[EnrichmentProvider] | None) -> list[Any]:
    if injected is not None:
        return [p for p in injected if entity_type in p.applies_to]
    return [cls for cls in DEFAULT_PROVIDERS if entity_type in cls.applies_to]


def enrich_entity(
    session: Session, case: Case, entity: Entity, user_id: int | None, data_dir,
    *, providers: list[EnrichmentProvider] | None = None,
) -> tuple[EnrichmentReport, dict[str, Any]]:
    """Consulta los proveedores aplicables y guarda entidades y relaciones nuevas como propuestas."""
    indicator = EntityRecord(
        type=entity.type, label=entity.label, props=clean_props(entity.props),
        confidence=entity.confidence, ref=f"db:{entity.id}",
    )
    if not applicable_providers(entity.type, providers):
        raise Invalid(f"No hay proveedores de enriquecimiento para entidades de tipo '{entity.type}'.")

    async def _run() -> EnrichmentReport:
        return await enrich_indicator(indicator, providers)

    report = asyncio.run(_run())  # antes de tocar la base: la red no debe retener el candado de auditoría

    value = indicator_value(indicator)
    raw = {
        "indicador": {"tipo": entity.type, "valor": value},
        "resultados": [
            {"proveedor": r.provider, "estado": r.status, "veredicto": r.verdict, "etiquetas": r.labels,
             "mensaje": r.message, "crudo": r.raw}
            for r in report.results
        ],
    }
    source = make_source(
        session, case.id, user_id, kind="enrichment", connector="cti-enrichment",
        reference=f"{entity.type}:{value}", payload=canonical_json(raw), data_dir=data_dir,
    )
    # El indicador va en el lote: se reutiliza la entidad existente y sirve de extremo de las relaciones
    summary, _ = persist_graph(
        session, case.id, [indicator, *report.entities], report.relations, source_id=source.id,
    )
    summary.entities_existing -= 1  # el propio indicador siempre existe: no es un hallazgo del proveedor
    return report, {"source_id": source.id, "summary": summary}


# ---------------------------------------------------------------- exportación


def build_cti_case(session: Session, case: Case, *, include_pending: bool) -> CtiCase:
    """Caso listo para exportar. Ver el módulo: confirmados por defecto, pendientes solo si se pide."""
    statuses = ("confirmed", "proposed") if include_pending else ("confirmed",)
    entities = session.execute(
        select(Entity).where(Entity.case_id == case.id, Entity.status.in_(statuses)).order_by(Entity.id)
    ).scalars().all()
    ids = {e.id for e in entities}
    records = [
        EntityRecord(type=e.type, label=e.label, props=clean_props(e.props), confidence=e.confidence,
                     ref=f"e{e.id}")
        for e in entities
    ]
    relations = session.execute(
        select(Relation).where(Relation.case_id == case.id, Relation.status.in_(statuses)).order_by(Relation.id)
    ).scalars().all()
    relation_records = [
        RelationRecord(src_ref=f"e{r.src_id}", dst_ref=f"e{r.dst_id}", type=r.type,
                       props=clean_props(r.props), confidence=r.confidence)
        for r in relations if r.src_id in ids and r.dst_id in ids
    ]
    if include_pending:
        relation_records += _pending_hypotheses(session, case.id, ids, relation_records)

    techniques = _technique_scores(session, case.id, include_pending=include_pending)
    return CtiCase(
        case_id=str(case.id), name=case.name, description=case.description or "", tlp=case.tlp,
        created_at=as_utc(case.created_at), entities=records, relations=relation_records,
        techniques=techniques,
    )


def _pending_hypotheses(session: Session, case_id: int, entity_ids: set[int],
                        existing: list[RelationRecord]) -> list[RelationRecord]:
    """Vínculos de MENARD en revisión, como `same_operator` pendiente (sin duplicar una relación ya existente)."""
    entity_of = dict(session.execute(select(Account.id, Account.entity_id).where(Account.case_id == case_id)).all())
    linked = {frozenset((r.src_ref, r.dst_ref)) for r in existing if r.type == SAME_OPERATOR}
    out: list[RelationRecord] = []
    links = session.execute(
        select(AccountLink).where(AccountLink.case_id == case_id, AccountLink.review_status == "pending")
        .order_by(AccountLink.id)
    ).scalars().all()
    for link in links:
        a, b = entity_of.get(link.account_a_id), entity_of.get(link.account_b_id)
        if a is None or b is None or a == b or a not in entity_ids or b not in entity_ids:
            continue
        pair = frozenset((f"e{a}", f"e{b}"))
        if pair in linked:
            continue
        linked.add(pair)
        out.append(RelationRecord(
            src_ref=f"e{a}", dst_ref=f"e{b}", type=SAME_OPERATOR, confidence=link.score,
            props={"description": PENDING_HYPOTHESIS, "estado": "pendiente", "menard_link_id": link.id},
        ))
    return out


def _technique_scores(session: Session, case_id: int, *, include_pending: bool) -> list[TechniqueScore]:
    """Una entrada por técnica (la más reciente gana). Sin `include_pending`, solo las confirmadas."""
    rows = session.execute(
        select(Finding).where(Finding.case_id == case_id, Finding.kind == "attack").order_by(Finding.id)
    ).scalars().all()
    latest: dict[str, TechniqueScore] = {}
    for row in rows:
        state = (row.chunk or {}).get("estado", STATUS_CONFIRMED)
        if not include_pending and state != STATUS_CONFIRMED:
            continue
        score = (row.chunk or {}).get("score")
        latest[row.value] = TechniqueScore(technique_id=row.value, score=score, comment=row.note or "")
    return list(latest.values())


def export_stix(cti_case: CtiCase, max_tlp: TlpLevel | None) -> StixExport:
    return export_case(cti_case, max_tlp=max_tlp)


def export_misp_event(cti_case: CtiCase, max_tlp: TlpLevel | None) -> MispExport:
    return export_misp(cti_case, max_tlp=max_tlp)


# ---------------------------------------------------------------- importación STIX


def _attack_section(session: Session, case_id: int) -> CaseSection | None:
    return session.execute(
        select(CaseSection).where(CaseSection.case_id == case_id, CaseSection.name == ATTACK_SECTION)
    ).scalars().first()


def link_technique(
    session: Session, case: Case, technique_id: str, *, score: float | None, comment: str,
    user_id: int | None, status: str = STATUS_CONFIRMED, source_id: int | None = None,
) -> tuple[Finding, bool]:
    """Vincula una técnica ATT&CK al caso. Si ya estaba vinculada, actualiza puntaje y comentario.
    Devuelve (hallazgo, si se creó)."""
    tid = technique_id.strip().upper()
    if not TECHNIQUE_ID_RE.match(tid):
        raise Invalid(f"ID ATT&CK con formato inválido: {technique_id!r}.")
    index = load_index()
    technique = index.get(tid)
    if technique is None:
        raise Invalid(f"El ID {tid} no existe en ATT&CK {index.version} (versión embebida).")
    ensure_default_sections(session, case.id)
    existing = session.execute(
        select(Finding).where(Finding.case_id == case.id, Finding.kind == "attack", Finding.value == tid)
        .order_by(Finding.id)
    ).scalars().first()
    if existing is not None:
        chunk = dict(existing.chunk or {})
        chunk.update(score=score, estado=status)
        existing.chunk = chunk
        existing.note = comment
        session.flush()
        return existing, False
    chunk = Datachunk(
        kind="attack", value=tid, quote=technique.name, context=", ".join(technique.tactics),
        page_url=technique.url, page_title=f"MITRE ATT&CK {tid}", platform="mitre-attack",
        detected_by="manual", captured_at=datetime.now(UTC),
    ).model_dump(mode="json")
    chunk.update(score=score, estado=status)
    if source_id is None:  # procedencia: el analista la vinculó a mano, contra el índice embebido
        source = Source(case_id=case.id, kind="manual", connector="attack",
                        reference=f"MITRE ATT&CK {index.version} (enterprise) · {tid}", created_by=user_id)
        session.add(source)
        session.flush()
        source_id = source.id
    section = _attack_section(session, case.id)
    finding = Finding(
        case_id=case.id, section_id=section.id if section else None, source_id=source_id, kind="attack",
        value=tid, chunk=chunk, note=comment, created_by=user_id,
    )
    session.add(finding)
    session.flush()
    return finding, True


def import_bundle_to_graph(session: Session, case: Case, bundle: dict[str, Any], user_id: int | None,
                           data_dir) -> dict[str, Any]:
    """Importa un Bundle STIX 2.1 como entidades, relaciones y técnicas en estado propuesto."""
    try:
        result = import_bundle(bundle)
    except Exception as exc:  # cualquier error de parseo de un documento ajeno es un 400
        raise Invalid(f"El documento no es un bundle STIX 2.1 válido: {exc}") from exc
    objects = bundle.get("objects") or []
    source = make_source(
        session, case.id, user_id, kind="upload", connector="stix-import",
        reference=f"bundle STIX 2.1 ({len(objects)} objetos)", payload=canonical_json(bundle),
        data_dir=data_dir,
    )
    summary, _ = persist_graph(session, case.id, result.entities, result.relations, source_id=source.id)
    techniques_new = techniques_known = 0
    for score in result.techniques:
        if not _can_propose(session, case.id, score.technique_id):
            techniques_known += 1  # el analista ya la confirmó: no se propone de nuevo
            continue
        _finding, created = link_technique(
            session, case, score.technique_id, score=score.score, comment=score.comment,
            user_id=user_id, status=STATUS_PROPOSED, source_id=source.id,
        )
        if created:
            techniques_new += 1
        else:
            techniques_known += 1
    meta = result.case
    return {
        "source_id": source.id,
        "summary": summary,
        "case_meta": {"nombre": meta.name, "descripcion": meta.description,
                      "tlp": meta.tlp} if meta is not None else None,
        "techniques_proposed": techniques_new,
        "techniques_known": techniques_known,
        "skipped": result.skipped[:50],
        "skipped_total": len(result.skipped),
    }


def _can_propose(session: Session, case_id: int, technique_id: str) -> bool:
    """No se propone una técnica que el analista ya confirmó."""
    tid = technique_id.strip().upper()
    current = session.execute(
        select(Finding).where(Finding.case_id == case_id, Finding.kind == "attack", Finding.value == tid)
    ).scalars().first()
    return current is None or (current.chunk or {}).get("estado") != STATUS_CONFIRMED


# ---------------------------------------------------------------- ATT&CK


def search_techniques(query: str, limit: int) -> list[dict[str, Any]]:
    index: AttackIndex = load_index()
    return [_technique_dict(index, t) for t in index.search(query, limit=limit)]


def _technique_dict(index: AttackIndex, tech: Technique) -> dict[str, Any]:
    return {
        "id": tech.id, "nombre": tech.name, "tacticas": list(tech.tactics),
        "nombres_tacticas": [index.tactic(t).name if index.tactic(t) else t for t in tech.tactics],
        "subtecnica": tech.is_subtechnique, "padre": tech.parent, "url": tech.url,
    }


def attack_layer(session: Session, case: Case, *, include_pending: bool) -> dict[str, Any]:
    entries = _technique_scores(session, case.id, include_pending=include_pending)
    return navigator_layer(
        entries, name=case.name[:200],
        description=f"Técnicas ATT&CK vinculadas al caso. Pendientes incluidas: "
                    f"{'sí' if include_pending else 'no'}.",
    )


# ---------------------------------------------------------------- datachunks sin LLM


def _sentence(text: str, pos: int, length: int) -> str:
    if pos < 0:
        return ""
    breaks = ("\n", ". ", "! ", "? ")
    start = max((text.rfind(b, 0, pos) + len(b) for b in breaks if text.rfind(b, 0, pos) >= 0), default=0)
    ends = [text.find(b, pos + length) for b in breaks]
    end = min((e for e in ends if e >= 0), default=len(text))
    return " ".join(text[start:end].split())[:300]


def _locate(lower_text: str, original: str, candidates: list[str]) -> tuple[str, int]:
    for candidate in candidates:
        if not candidate:
            continue
        pos = lower_text.find(candidate.lower())
        if pos >= 0:
            return original[pos: pos + len(candidate)], pos
    return candidates[0] if candidates else "", -1


def chunks_from_text(text: str, *, page_url: str, page_title: str, platform: str,
                     captured_at: datetime) -> list[Datachunk]:
    """Datachunks de un texto: IOCs del extractor y reglas argentinas de FUNES. Sin red ni LLM."""
    lower = text.lower()
    found: dict[tuple[str, str], tuple[int, Datachunk]] = {}

    def add(kind: str, value: str, quote: str, pos: int, detected_by: str) -> None:
        key = (kind, value.lower().rstrip("/"))
        if key in found or len(found) >= MAX_CHUNKS:
            return
        chunk = Datachunk(
            kind=kind, value=value, quote=quote, context=_sentence(text, pos, len(quote)),
            page_url=page_url, page_title=page_title, platform=platform or "generic",
            detected_by=detected_by, captured_at=captured_at,
        )
        found[key] = (pos if pos >= 0 else len(text), chunk)

    for entity in extract_iocs(text).entities:
        quote, pos = _locate(lower, text, [entity.label, str(entity.props.get("defanged", ""))])
        add(entity.type, entity.label, quote, pos, "rule")
    rule_hits, _dates = find_rule_hits(text)
    for hit in rule_hits:
        pos = hit.start
        quote = text[hit.start:hit.end] or hit.quote
        add(hit.type, hit.label, quote, pos, "funes")
    return [chunk for _, chunk in sorted(found.values(), key=lambda pair: pair[0])]


__all__ = [
    "ATTACK_SECTION", "applicable_providers", "attack_layer", "build_cti_case", "chunks_from_text",
    "enrich_entity", "export_misp_event", "export_stix", "extract_iocs_to_graph", "import_bundle_to_graph",
    "link_technique", "search_techniques",
]
