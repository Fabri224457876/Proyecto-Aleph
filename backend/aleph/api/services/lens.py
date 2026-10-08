"""Aleph Lens (extensión de navegador): captura en vivo, consulta de handles, incisos y hallazgos.

Sin FastAPI. Igual que el resto de los servicios: auditan en la misma transacción y no hacen commit.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aleph.core.models import (
    Account,
    AccountLink,
    CaseSection,
    Entity,
    Finding,
    Post,
    Relation,
    Source,
)
from aleph.core.schemas import (
    DEFAULT_SECTIONS,
    ENTITY_TYPES,
    AccountProfile,
    AccountRecord,
    CaptureBatch,
    CollectionResult,
    FindingCreate,
    HandleInfo,
    HandleLookupRequest,
    HandleLookupResponse,
    RelationRecord,
    SectionOut,
)

from .. import auditlog
from ..errors import Conflict, Invalid
from .ingest import account_entity_label, ingest_collection
from .util import as_utc, get_in_case

CONNECTOR = "lens"
UNCLASSIFIED = "sin clasificar"
MAX_LOOKUP_HANDLES = 500
FINDING_RELATION = "related_to"


class CaptureSummary(BaseModel):
    case_id: int
    source_id: int
    source_created: bool
    accounts_new: int = 0
    accounts_known: int = 0
    posts_new: int = 0
    posts_known: int = 0
    relations_new: int = 0
    relations_known: int = 0
    warnings: list[str] = Field(default_factory=list)


def clean_handle(handle: str) -> str:
    return handle.strip().lstrip("@")


def capture_to_collection(batch: CaptureBatch) -> CollectionResult:
    """Convierte lo que mandó la extensión en un `CollectionResult` del conector `lens`."""
    default_platform = batch.platform.strip().lower() or "generic"
    profiles: dict[str, AccountProfile] = {}
    for profile in batch.profiles:
        account = profile.account.model_copy(update={
            "platform": profile.account.platform.strip().lower() or default_platform,
            "handle": clean_handle(profile.account.handle),
        })
        fixed = AccountProfile(account=account, posts=list(profile.posts))
        if fixed.key in profiles:
            profiles[fixed.key].posts.extend(fixed.posts)
        else:
            profiles[fixed.key] = fixed
    relations = []
    for src, dst, kind in batch.interactions:
        src, dst, kind = clean_handle(src), clean_handle(dst), kind.strip().lower()
        if not src or not dst or not kind or src.lower() == dst.lower():
            continue
        keys = []
        for handle in (src, dst):  # una cuenta que solo aparece en una interacción también entra al caso
            stub = AccountProfile(account=AccountRecord(platform=default_platform, handle=handle))
            profiles.setdefault(stub.key, stub)
            keys.append(stub.key)
        relations.append(RelationRecord(src_ref=keys[0], dst_ref=keys[1], type=kind, props={"origin": CONNECTOR}))
    # El crudo no incluye la hora: reenviar la misma pantalla no genera otra fuente
    raw = batch.model_dump(mode="json", exclude={"captured_at"})
    return CollectionResult(
        connector=CONNECTOR, reference=batch.page_url, retrieved_at=batch.captured_at,
        profiles=list(profiles.values()), relations=relations, raw=raw,
    )


def capture_batch(
    session: Session, case_id: int, batch: CaptureBatch, user_id: int | None = None,
    *, data_dir: str | Path | None = None,
) -> CaptureSummary:
    """Persiste una captura de la extensión con `ingest_collection` (idempotente)."""
    if not batch.page_url.strip():
        raise Invalid("La captura no indica la URL de la página.")
    summary = ingest_collection(session, case_id, capture_to_collection(batch), user_id, data_dir=data_dir)
    return CaptureSummary(
        case_id=case_id, source_id=summary.source_id, source_created=summary.source_created,
        accounts_new=summary.accounts_created, accounts_known=summary.accounts_updated,
        posts_new=summary.posts_created, posts_known=summary.posts_updated + summary.posts_unchanged,
        relations_new=summary.relations_created, relations_known=summary.relations_existing,
        warnings=summary.warnings,
    )


def lookup_handles(session: Session, case_id: int, request: HandleLookupRequest) -> HandleLookupResponse:
    """Qué sabe el caso de cada handle. Cantidad fija de consultas, sin importar cuántos handles vengan."""
    if len(request.handles) > MAX_LOOKUP_HANDLES:
        raise Invalid(f"Se pueden consultar hasta {MAX_LOOKUP_HANDLES} handles por pedido.")
    platform = request.platform.strip().lower()
    wanted = {h: clean_handle(h).lower() for h in request.handles}
    response = HandleLookupResponse(handles={h: HandleInfo() for h in wanted})
    lowered = sorted({v for v in wanted.values() if v})
    if not lowered:
        return response

    accounts = session.execute(
        select(Account).where(
            Account.case_id == case_id, Account.platform == platform, func.lower(Account.handle).in_(lowered)
        )
    ).scalars().all()
    if not accounts:
        return response
    by_handle = {a.handle.lower(): a for a in accounts}
    account_ids = [a.id for a in accounts]
    entity_ids = [a.entity_id for a in accounts if a.entity_id is not None]

    post_counts = dict(session.execute(
        select(Post.account_id, func.count(Post.id)).where(Post.account_id.in_(account_ids)).group_by(Post.account_id)
    ).all())
    links = session.execute(
        select(AccountLink).where(
            AccountLink.case_id == case_id,
            or_(AccountLink.account_a_id.in_(account_ids), AccountLink.account_b_id.in_(account_ids)),
        ).order_by(AccountLink.score.desc())
    ).scalars().all()
    other_ids = ({l.account_a_id for l in links} | {l.account_b_id for l in links}) - set(account_ids)
    others = {a.id: a for a in accounts}
    if other_ids:
        others.update({
            a.id: a for a in session.execute(select(Account).where(Account.id.in_(other_ids))).scalars()
        })
    relations, labels, notes = [], {}, {}
    if entity_ids:
        relations = session.execute(
            select(Relation).where(
                Relation.case_id == case_id, Relation.status != "rejected",
                or_(Relation.src_id.in_(entity_ids), Relation.dst_id.in_(entity_ids)),
            ).order_by(Relation.id)
        ).scalars().all()
        neighbor_ids = {r.src_id for r in relations} | {r.dst_id for r in relations}
        if neighbor_ids:
            labels = dict(session.execute(
                select(Entity.id, Entity.label).where(Entity.id.in_(neighbor_ids))
            ).all())
        for entity_id, note in session.execute(
            select(Finding.entity_id, Finding.note).where(
                Finding.case_id == case_id, Finding.entity_id.in_(entity_ids), Finding.note != ""
            ).order_by(Finding.id)
        ):
            notes[entity_id] = note  # queda la más reciente

    links_by_account: dict[int, list[dict]] = {}
    tracked_accounts = set(account_ids)
    for link in links:
        for mine, other in ((link.account_a_id, link.account_b_id), (link.account_b_id, link.account_a_id)):
            if mine in tracked_accounts:
                other_account = others.get(other)
                if other_account is None:
                    continue
                links_by_account.setdefault(mine, []).append({
                    "link_id": link.id, "other": other_account.handle, "platform": other_account.platform,
                    "score": link.score, "review_status": link.review_status,
                })
    relations_by_entity: dict[int, list[dict]] = {}
    tracked = set(entity_ids)
    for rel in relations:
        for mine, other, direction in ((rel.src_id, rel.dst_id, "out"), (rel.dst_id, rel.src_id, "in")):
            if mine in tracked:
                relations_by_entity.setdefault(mine, []).append({
                    "relation_id": rel.id, "type": rel.type, "label": labels.get(other, ""), "entity_id": other,
                    "direction": direction, "status": rel.status, "confidence": rel.confidence,
                })

    for original, lowered_handle in wanted.items():
        account = by_handle.get(lowered_handle)
        if account is None:
            continue
        response.handles[original] = HandleInfo(
            known=True, entity_id=account.entity_id, posts_captured=post_counts.get(account.id, 0),
            links=links_by_account.get(account.id, []),
            relations=relations_by_entity.get(account.entity_id, []),
            note=notes.get(account.entity_id, ""),
        )
    return response


# ---------------------------------------------------------------- incisos

def ensure_default_sections(session: Session, case_id: int) -> tuple[list[CaseSection], bool]:
    """Crea los incisos de `DEFAULT_SECTIONS` si el caso todavía no tiene ninguno.

    Devuelve (incisos ordenados, si se crearon recién).
    """
    existing = session.execute(
        select(CaseSection).where(CaseSection.case_id == case_id).order_by(CaseSection.position, CaseSection.id)
    ).scalars().all()
    if existing:
        return list(existing), False
    auditlog.guard(session)
    created = [CaseSection(case_id=case_id, name=name, position=i) for i, name in enumerate(DEFAULT_SECTIONS)]
    session.add_all(created)
    session.flush()
    return created, True


def unclassified_section(session: Session, case_id: int) -> CaseSection | None:
    return session.execute(
        select(CaseSection).where(CaseSection.case_id == case_id, func.lower(CaseSection.name) == UNCLASSIFIED)
    ).scalars().first()


def sections_out(session: Session, case_id: int, sections: list[CaseSection]) -> list[SectionOut]:
    """Incisos con su contador. Los hallazgos sin inciso se cuentan en "Sin clasificar" si existe."""
    counts = {
        section_id: n for section_id, n in session.execute(
            select(Finding.section_id, func.count(Finding.id)).where(Finding.case_id == case_id)
            .group_by(Finding.section_id)
        )
    }
    out = []
    for section in sections:
        n = counts.get(section.id, 0)
        if section.name.lower() == UNCLASSIFIED:
            n += counts.get(None, 0)
        out.append(SectionOut(id=section.id, name=section.name, position=section.position, findings=n))
    return out


def delete_section(session: Session, case_id: int, section_id: int, user_id: int | None = None) -> int:
    """Borra el inciso. Sus hallazgos no se borran: pasan a sin clasificar. Devuelve cuántos se movieron."""
    auditlog.guard(session)
    section = get_in_case(session, CaseSection, case_id, section_id, "El inciso")
    fallback = unclassified_section(session, case_id)
    new_id = fallback.id if fallback is not None and fallback.id != section.id else None
    findings = session.execute(select(Finding).where(Finding.section_id == section.id)).scalars().all()
    for finding in findings:
        finding.section_id = new_id
    session.flush()
    name = section.name
    session.delete(section)
    session.flush()
    auditlog.record(session, "section.delete", user_id=user_id, case_id=case_id, target=f"section:{section_id}",
                    detail={"name": name, "findings_moved": len(findings), "moved_to": new_id})
    return len(findings)


# ---------------------------------------------------------------- hallazgos

@dataclass
class FindingResult:
    finding: Finding
    entity_created: bool = False
    relation_id: int | None = None


def finding_hash(quote: str, context: str) -> str:
    """sha256 en hexadecimal de `quote + "\\n" + context` en UTF-8."""
    return hashlib.sha256(f"{quote}\n{context}".encode()).hexdigest()


def _find_or_create_entity(session: Session, case_id: int, kind: str, value: str, platform: str,
                           source_id: int) -> tuple[Entity, bool]:
    props: dict = {}
    label = value[:500]
    if kind == "account":
        handle = clean_handle(value)
        platform = platform.strip().lower() or "generic"
        account = session.execute(
            select(Account).where(
                Account.case_id == case_id, Account.platform == platform,
                func.lower(Account.handle) == handle.lower(),
            )
        ).scalars().first()
        if account is not None and account.entity_id is not None:
            entity = session.get(Entity, account.entity_id)
            if entity is not None:
                return entity, False
        label = account_entity_label(platform, handle)[:500]
        props = {"platform": platform, "handle": handle}
    entity = session.execute(
        select(Entity).where(
            Entity.case_id == case_id, Entity.type == kind, func.lower(Entity.label) == label.lower()
        ).order_by(Entity.id)
    ).scalars().first()
    if entity is not None:
        return entity, False
    entity = Entity(case_id=case_id, type=kind, label=label, props=props, confidence=1.0,
                    status="confirmed", source_id=source_id)
    session.add(entity)
    session.flush()
    return entity, True


def create_finding(session: Session, case_id: int, data: FindingCreate, user_id: int | None = None) -> FindingResult:
    """Incorpora al expediente un datachunk que el analista soltó en un inciso.

    Crea la `Source` (URL, hora y hash de la cita). Si el chunk es de un tipo de entidad, busca
    o crea esa entidad como `confirmed` (la incorporó una persona) y la vincula; si además se soltó
    sobre otra entidad, las relaciona. Un chunk `text` soltado sobre una entidad queda como nota suya.
    """
    chunk = data.chunk
    kind = chunk.kind.strip().lower()
    value = chunk.value.strip()
    if kind != "text" and kind not in ENTITY_TYPES:
        raise Invalid(f"Tipo de hallazgo inválido. Valores permitidos: text, {', '.join(ENTITY_TYPES)}.")
    if not value:
        raise Invalid("El hallazgo no tiene valor.")
    if not chunk.page_url.strip():
        raise Invalid("El hallazgo no indica la URL de la página.")
    auditlog.guard(session)
    section_id = data.section_id
    if section_id is not None:
        get_in_case(session, CaseSection, case_id, section_id, "El inciso")
    else:
        fallback = unclassified_section(session, case_id)
        section_id = fallback.id if fallback is not None else None
    target = None
    if data.attach_to_entity_id is not None:
        target = get_in_case(session, Entity, case_id, data.attach_to_entity_id, "La entidad")

    sha = finding_hash(chunk.quote, chunk.context)
    source = Source(
        case_id=case_id, kind="manual", connector=CONNECTOR, reference=chunk.page_url.strip(),
        sha256=sha, retrieved_at=as_utc(chunk.captured_at), created_by=user_id,
    )
    session.add(source)
    session.flush()

    entity, entity_created, relation = None, False, None
    promoted = False
    if kind != "text":
        entity, entity_created = _find_or_create_entity(session, case_id, kind, value, chunk.platform, source.id)
        if entity.status != "confirmed":
            entity.status, promoted = "confirmed", True
        if target is not None and target.id != entity.id:
            relation = session.execute(
                select(Relation).where(
                    Relation.case_id == case_id, Relation.type == FINDING_RELATION,
                    or_(
                        (Relation.src_id == target.id) & (Relation.dst_id == entity.id),
                        (Relation.src_id == entity.id) & (Relation.dst_id == target.id),
                    ),
                )
            ).scalars().first()
            if relation is None:
                relation = Relation(
                    case_id=case_id, src_id=target.id, dst_id=entity.id, type=FINDING_RELATION,
                    props={"origin": CONNECTOR}, confidence=1.0, status="confirmed", source_id=source.id,
                )
                session.add(relation)
            elif relation.status != "confirmed":
                relation.status = "confirmed"
    elif target is not None:
        entity = target

    finding = Finding(
        case_id=case_id, section_id=section_id, entity_id=entity.id if entity is not None else None,
        source_id=source.id, kind=kind, value=value, chunk=chunk.model_dump(mode="json"), note=data.note,
        created_by=user_id,
    )
    session.add(finding)
    session.flush()
    if relation is not None and "finding_id" not in (relation.props or {}):
        relation.props = {**(relation.props or {}), "finding_id": finding.id}
        session.flush()
    auditlog.record(
        session, "finding.create", user_id=user_id, case_id=case_id, target=f"finding:{finding.id}",
        detail={
            "kind": kind, "value": value[:200], "page_url": chunk.page_url, "sha256": sha,
            "section_id": section_id, "source_id": source.id, "entity_id": finding.entity_id,
            "entity_created": entity_created, "entity_promoted": promoted,
            "relation_id": relation.id if relation is not None else None,
            "detected_by": chunk.detected_by,
        },
    )
    return FindingResult(finding=finding, entity_created=entity_created,
                         relation_id=relation.id if relation is not None else None)


__all__ = [
    "CaptureSummary",
    "Conflict",
    "FindingResult",
    "capture_batch",
    "capture_to_collection",
    "create_finding",
    "delete_section",
    "ensure_default_sections",
    "finding_hash",
    "lookup_handles",
    "sections_out",
    "unclassified_section",
]
