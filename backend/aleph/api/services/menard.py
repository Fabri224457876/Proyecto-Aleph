"""Orquestación de MENARD sobre un caso. Sin FastAPI: hoy lo llama el endpoint, mañana un worker.

MENARD produce hipótesis ("posible mismo operador, por estas razones"), nunca veredictos:
la relación `same_operator` solo entra al grafo cuando una persona confirma el vínculo.
"""

import asyncio
import inspect
from collections.abc import Callable

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aleph.core.models import Account, AccountLink, Post, Relation, Source
from aleph.core.schemas import AccountProfile, MenardReport

from .. import auditlog
from ..errors import Conflict, EngineFailure, EngineUnavailable, Invalid
from .ingest import account_to_record, ensure_account_entity, post_to_record
from .util import clamp01, get_in_case

SAME_OPERATOR = "same_operator"
DISCLAIMER = "Los resultados de MENARD son hipótesis con evidencia para revisión humana, no veredictos."


class ClusterSummary(BaseModel):
    members: list[str]
    account_ids: list[int]
    cohesion: float
    summary: str = ""


class LinkBrief(BaseModel):
    link_id: int
    a: str
    b: str
    score: float
    confidence: str = ""
    review_status: str


class MenardRunSummary(BaseModel):
    case_id: int
    job_id: int | None = None
    accounts_analyzed: int
    pairs_returned: int
    links_created: int = 0
    links_updated: int = 0
    links_below_min_score: int = 0
    top: list[LinkBrief] = Field(default_factory=list)
    clusters: list[ClusterSummary] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict)
    params: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


def resolve_engine() -> Callable[[list[AccountProfile]], MenardReport]:
    try:
        from aleph.menard import analyze
    except ImportError as exc:
        raise EngineUnavailable(
            "El motor MENARD no está disponible en esta instalación (falta aleph.menard.analyze)."
        ) from exc
    return analyze


def load_profiles(
    session: Session, case_id: int, account_ids: list[int] | None = None
) -> tuple[list[AccountProfile], dict[str, int]]:
    """Cuentas del caso como `AccountProfile`, y el mapa clave de perfil -> id de cuenta."""
    stmt = select(Account).where(Account.case_id == case_id).order_by(Account.id)
    if account_ids is not None:
        stmt = stmt.where(Account.id.in_(account_ids))
    accounts = session.execute(stmt).scalars().all()
    if account_ids is not None:
        missing = sorted(set(account_ids) - {a.id for a in accounts})
        if missing:
            raise Invalid(f"Estas cuentas no pertenecen al caso: {missing}.")
    profiles, key_to_id = [], {}
    for account in accounts:
        posts = session.execute(
            select(Post).where(Post.account_id == account.id).order_by(Post.created_at, Post.id)
        ).scalars().all()
        profile = AccountProfile(account=account_to_record(account), posts=[post_to_record(p) for p in posts])
        profiles.append(profile)
        key_to_id[profile.key] = account.id
    return profiles, key_to_id


async def _await(awaitable):
    return await awaitable


def run_menard(
    session: Session,
    case_id: int,
    user_id: int | None = None,
    *,
    analyze: Callable | None = None,
    account_ids: list[int] | None = None,
    min_score: float = 0.0,
) -> MenardRunSummary:
    """Corre MENARD sobre las cuentas del caso y guarda/actualiza un `AccountLink` por par.

    `analyze` permite inyectar el motor; si no se pasa, se importa `aleph.menard.analyze`.
    Un vínculo ya revisado conserva su revisión aunque cambie el puntaje. No hace commit.
    """
    profiles, key_to_id = load_profiles(session, case_id, account_ids)
    if len(profiles) < 2:
        raise Conflict("MENARD necesita al menos dos cuentas en el caso para comparar.")
    engine = analyze or resolve_engine()
    try:
        report = engine(profiles)
        if inspect.isawaitable(report):
            report = asyncio.run(_await(report))
        report = MenardReport.model_validate(report, from_attributes=True)
    except Exception as exc:  # el motor es código ajeno a la API: cualquier fallo se informa igual
        raise EngineFailure(f"El motor MENARD falló: {exc}") from exc

    summary = MenardRunSummary(
        case_id=case_id, accounts_analyzed=len(profiles), pairs_returned=len(report.pairs),
        skipped=dict(report.skipped), params=dict(report.params),
    )
    auditlog.guard(session)
    touched: dict[int, tuple[AccountLink, str, str, str]] = {}
    for pair in report.pairs:
        a_id, b_id = key_to_id.get(pair.a), key_to_id.get(pair.b)
        if a_id is None or b_id is None or a_id == b_id:
            summary.warnings.append(f"Par {pair.a!r} / {pair.b!r} con cuentas desconocidas; se omitió.")
            continue
        score = clamp01(pair.score)
        if score < min_score:
            summary.links_below_min_score += 1
            continue
        key_a, key_b = pair.a, pair.b
        if a_id > b_id:
            a_id, b_id, key_a, key_b = b_id, a_id, key_b, key_a
        link = session.execute(
            select(AccountLink).where(AccountLink.account_a_id == a_id, AccountLink.account_b_id == b_id)
        ).scalars().first()
        if link is None:
            link = AccountLink(case_id=case_id, account_a_id=a_id, account_b_id=b_id, score=score)
            session.add(link)
            summary.links_created += 1
        else:
            link.score = score
            if link.id not in touched:
                summary.links_updated += 1
        link.signals = pair.model_dump(mode="json")
        session.flush()
        touched[link.id] = (link, key_a, key_b, pair.confidence)

    ranked = sorted(touched.values(), key=lambda t: t[0].score, reverse=True)
    summary.top = [
        LinkBrief(link_id=link.id, a=a, b=b, score=link.score, confidence=conf, review_status=link.review_status)
        for link, a, b, conf in ranked[:10]
    ]
    summary.clusters = [
        ClusterSummary(
            members=c.members, account_ids=[key_to_id[m] for m in c.members if m in key_to_id],
            cohesion=c.cohesion, summary=c.summary,
        )
        for c in report.clusters
    ]
    auditlog.record(
        session, "menard.run", user_id=user_id, case_id=case_id, target=f"case:{case_id}",
        detail={
            "accounts": sorted(key_to_id.values()), "pairs": len(report.pairs),
            "links_created": summary.links_created, "links_updated": summary.links_updated,
            "min_score": min_score,
        },
    )
    return summary


def find_same_operator(session: Session, case_id: int, entity_a: int, entity_b: int) -> Relation | None:
    return session.execute(
        select(Relation).where(
            Relation.case_id == case_id, Relation.type == SAME_OPERATOR,
            or_(
                (Relation.src_id == entity_a) & (Relation.dst_id == entity_b),
                (Relation.src_id == entity_b) & (Relation.dst_id == entity_a),
            ),
        ).order_by(Relation.id)
    ).scalars().first()


def review_link(
    session: Session, case_id: int, link_id: int, user_id: int, decision: str, note: str = ""
) -> tuple[AccountLink, Relation | None]:
    """Confirma o rechaza una hipótesis de MENARD.

    Al confirmar crea (o reactiva) la relación `same_operator` entre las entidades de ambas
    cuentas, con el puntaje como confianza. Al rechazar un vínculo antes confirmado, esa
    relación pasa a `rejected`. No hace commit.
    """
    if decision not in ("confirm", "reject"):
        raise Invalid("La decisión debe ser 'confirm' o 'reject'.")
    auditlog.guard(session)
    link = get_in_case(session, AccountLink, case_id, link_id, "El vínculo")
    new_status = "confirmed" if decision == "confirm" else "rejected"
    if link.review_status == new_status:
        raise Conflict("El vínculo ya tiene esa revisión.")
    previous = link.review_status
    entity_a = ensure_account_entity(session, session.get(Account, link.account_a_id))
    entity_b = ensure_account_entity(session, session.get(Account, link.account_b_id))
    relation = None
    if entity_a.id != entity_b.id:
        relation = find_same_operator(session, case_id, entity_a.id, entity_b.id)
    if decision == "confirm":
        if entity_a.id == entity_b.id:
            raise Conflict("Las dos cuentas ya están representadas por la misma entidad del grafo.")
        props = {"account_link_id": link.id, "reviewed_by": user_id, "review_note": note, "origin": "menard"}
        if relation is None:
            source = Source(
                case_id=case_id, kind="menard", connector="menard",
                reference=f"account_link:{link.id}", created_by=user_id,
            )
            session.add(source)
            session.flush()
            relation = Relation(
                case_id=case_id, src_id=entity_a.id, dst_id=entity_b.id, type=SAME_OPERATOR,
                props=props, confidence=link.score, status="confirmed", source_id=source.id,
            )
            session.add(relation)
        else:
            relation.props = {**(relation.props or {}), **props}
            relation.confidence = link.score
            relation.status = "confirmed"
    elif relation is not None and (relation.props or {}).get("account_link_id") == link.id:
        relation.status = "rejected"
    link.review_status = new_status
    link.reviewed_by = user_id
    link.review_note = note
    session.flush()
    auditlog.record(
        session, "menard.review", user_id=user_id, case_id=case_id, target=f"account_link:{link.id}",
        detail={
            "decision": decision, "previous": previous, "score": link.score, "note": note,
            "accounts": [link.account_a_id, link.account_b_id],
            "relation_id": relation.id if relation is not None else None,
        },
    )
    return link, relation
