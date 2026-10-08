"""MENARD: correr el análisis de multicuentas y revisar sus hipótesis."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Query
from sqlalchemy import or_, select

from aleph.core.models import AccountLink, Job

from .. import auditlog
from ..deps import DB, CurrentUser, Paging, ReadCase, WriteCase, page_of, paginate
from ..errors import EngineFailure
from ..presenters import links_out
from ..routing import make_router
from ..schemas import LinkOut, LinkReviewIn, LinkReviewOut, MenardRunIn, Message, Page, RelationOut
from ..services import MenardRunSummary, review_link, run_menard
from ..services.util import get_in_case

router = make_router(prefix="/cases/{case_id}/menard", tags=["menard"])


@router.post(
    "/run", response_model=MenardRunSummary, summary="Correr MENARD sobre las cuentas del caso",
    responses={502: {"model": Message, "description": "El motor falló"},
               503: {"model": Message, "description": "El motor no está instalado"}},
)
def run(case: WriteCase, session: DB, user: CurrentUser, body: MenardRunIn | None = None):
    """Sincrónico por ahora. Cada corrida deja un `Job` (kind `menard`) con el resumen.

    Guarda o actualiza un vínculo (`AccountLink`) por par de cuentas. Son hipótesis: nada
    entra al grafo hasta que una persona confirma el vínculo.
    """
    body = body or MenardRunIn()
    params = body.model_dump()
    try:
        summary = run_menard(session, case.id, user.id, account_ids=body.account_ids, min_score=body.min_score)
    except EngineFailure as exc:
        session.rollback()
        session.add(Job(case_id=case.id, kind="menard", status="failed", params=params, error=exc.message,
                        created_by=user.id, finished_at=datetime.now(UTC)))
        auditlog.record(session, "menard.run.failed", user_id=user.id, case_id=case.id,
                        target=f"case:{case.id}", detail={"error": exc.message[:500]})
        session.commit()
        raise
    job = Job(
        case_id=case.id, kind="menard", status="done", params=params, created_by=user.id,
        finished_at=datetime.now(UTC),
        result=summary.model_dump(mode="json", include={
            "accounts_analyzed", "pairs_returned", "links_created", "links_updated", "links_below_min_score",
        }),
    )
    session.add(job)
    session.flush()
    summary.job_id = job.id
    session.commit()
    return summary


@router.get("/links", response_model=Page[LinkOut], summary="Hipótesis de mismo operador, por puntaje")
def list_links(
    case: ReadCase, session: DB, user: CurrentUser, page: Paging,
    status: Annotated[Literal["pending", "confirmed", "rejected"] | None, Query(description="Estado de revisión")] = None,
    min_score: Annotated[float, Query(ge=0, le=1)] = 0.0,
    account_id: Annotated[int | None, Query(description="Solo vínculos de esta cuenta")] = None,
):
    stmt = select(AccountLink).where(AccountLink.case_id == case.id).order_by(AccountLink.score.desc(), AccountLink.id)
    if status:
        stmt = stmt.where(AccountLink.review_status == status)
    if min_score > 0:
        stmt = stmt.where(AccountLink.score >= min_score)
    if account_id is not None:
        stmt = stmt.where(or_(AccountLink.account_a_id == account_id, AccountLink.account_b_id == account_id))
    items, total = paginate(session, stmt, page)
    auditlog.record(session, "menard.links.view", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
                    detail={"status": status or "", "total": total})
    session.commit()
    return page_of(links_out(session, items), total, page)


@router.get("/links/{link_id}", response_model=LinkOut, summary="Ver un vínculo con su evidencia")
def get_link(link_id: int, case: ReadCase, session: DB):
    link = get_in_case(session, AccountLink, case.id, link_id, "El vínculo")
    return links_out(session, [link])[0]


@router.post("/links/{link_id}/review", response_model=LinkReviewOut,
             summary="Confirmar o rechazar una hipótesis de MENARD")
def review(link_id: int, body: LinkReviewIn, case: WriteCase, session: DB, user: CurrentUser):
    """Al confirmar se crea la relación `same_operator` entre las entidades de ambas cuentas,
    con el puntaje como confianza."""
    link, relation = review_link(session, case.id, link_id, user.id, body.decision, body.note)
    session.commit()
    return LinkReviewOut(
        link=links_out(session, [link])[0],
        relation=RelationOut.model_validate(relation) if relation is not None else None,
    )
