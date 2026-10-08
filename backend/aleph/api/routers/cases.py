"""Casos de investigación, su línea de tiempo y su exportación."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import HTTPException, Query, Response
from sqlalchemy import func, or_, select

from aleph.core.models import (
    Account,
    AccountLink,
    Case,
    CaseSection,
    Entity,
    Finding,
    Job,
    Post,
    Relation,
    Source,
)

from .. import auditlog
from ..deps import (
    DB,
    Admin,
    AnyUser,
    CurrentUser,
    ManageCase,
    Paging,
    ReadCase,
    WriteCase,
    Writer,
    page_of,
    paginate,
)
from ..presenters import accounts_out, links_out, source_out
from ..routing import make_router
from ..schemas import (
    CASE_STATUSES,
    CaseCreate,
    CaseDetail,
    CaseExport,
    CaseOut,
    CaseUpdate,
    EntityOut,
    Page,
    PostOut,
    RelationOut,
    TimelineItem,
    TimelineOut,
)
from ..services.lens import ensure_default_sections
from ..services.util import as_utc, like_pattern

router = make_router(prefix="/cases", tags=["casos"])

_EVENT_DATE_KEYS = ("date", "datetime", "timestamp", "when", "start", "start_date", "occurred_at", "fecha")


def _counts(session, case_id: int) -> dict[str, int]:
    def count(model, *where):
        return session.scalar(select(func.count(model.id)).where(*where)) or 0

    return {
        "entities": count(Entity, Entity.case_id == case_id),
        "entities_proposed": count(Entity, Entity.case_id == case_id, Entity.status == "proposed"),
        "relations": count(Relation, Relation.case_id == case_id),
        "sources": count(Source, Source.case_id == case_id),
        "accounts": count(Account, Account.case_id == case_id),
        "posts": session.scalar(
            select(func.count(Post.id)).join(Account, Post.account_id == Account.id).where(Account.case_id == case_id)
        ) or 0,
        "links": count(AccountLink, AccountLink.case_id == case_id),
        "links_pending": count(AccountLink, AccountLink.case_id == case_id, AccountLink.review_status == "pending"),
        "findings": count(Finding, Finding.case_id == case_id),
    }


@router.post("", response_model=CaseOut, status_code=201, summary="Abrir un caso")
def create_case(body: CaseCreate, session: DB, user: Writer):
    case = Case(name=body.name, description=body.description, legal_basis=body.legal_basis,
                tlp=body.tlp, created_by=user.id)
    session.add(case)
    session.flush()
    sections, _ = ensure_default_sections(session, case.id)
    auditlog.record(
        session, "case.create", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
        detail={"name": case.name, "tlp": case.tlp, "legal_basis": case.legal_basis,
                "sections": [s.name for s in sections]},
    )
    session.commit()
    return case


@router.get("", response_model=Page[CaseOut], summary="Listar casos")
def list_cases(
    session: DB, user: AnyUser, page: Paging,
    status: Annotated[Literal["open", "closed", "archived"] | None, Query()] = None,
    tlp: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(description="Texto en nombre o descripción")] = None,
):
    stmt = select(Case).order_by(Case.id.desc())
    if status:
        stmt = stmt.where(Case.status == status)
    if tlp:
        stmt = stmt.where(Case.tlp == tlp.strip().lower())
    if q and q.strip():
        pattern = like_pattern(q.strip())
        stmt = stmt.where(or_(Case.name.ilike(pattern, escape="\\"), Case.description.ilike(pattern, escape="\\")))
    items, total = paginate(session, stmt, page)
    return page_of(items, total, page)


@router.get("/{case_id}", response_model=CaseDetail, summary="Ver un caso (queda auditado)")
def get_case(case: ReadCase, session: DB, user: CurrentUser):
    detail = CaseDetail.model_validate(case)
    detail.counts = _counts(session, case.id)
    auditlog.record(session, "case.view", user_id=user.id, case_id=case.id, target=f"case:{case.id}")
    session.commit()
    return detail


@router.patch("/{case_id}", response_model=CaseOut, summary="Editar un caso abierto")
def update_case(body: CaseUpdate, case: WriteCase, session: DB, user: CurrentUser):
    changes = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None and getattr(case, field) != value:
            changes[field] = {"from": getattr(case, field), "to": value}
            setattr(case, field, value)
    if changes:
        auditlog.record(session, "case.update", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
                        detail={"changes": changes})
        session.commit()
    return case


def _transition(session, case: Case, user, new_status: str, allowed_from: tuple[str, ...], action: str) -> Case:
    if case.status not in allowed_from:
        raise HTTPException(status_code=409, detail=f"El caso está en estado '{case.status}': no admite esta acción.")
    previous, case.status = case.status, new_status
    auditlog.record(session, action, user_id=user.id, case_id=case.id, target=f"case:{case.id}",
                    detail={"from": previous, "to": new_status})
    session.commit()
    return case


@router.post("/{case_id}/close", response_model=CaseOut, summary="Cerrar un caso (queda de solo lectura)")
def close_case(case: ManageCase, session: DB, user: CurrentUser):
    return _transition(session, case, user, "closed", ("open",), "case.close")


@router.post("/{case_id}/archive", response_model=CaseOut, summary="Archivar un caso")
def archive_case(case: ManageCase, session: DB, user: CurrentUser):
    return _transition(session, case, user, "archived", ("open", "closed"), "case.archive")


@router.post("/{case_id}/reopen", response_model=CaseOut, summary="Reabrir un caso cerrado o archivado (admin)")
def reopen_case(case: ManageCase, session: DB, admin: Admin):
    return _transition(session, case, admin, "open", ("closed", "archived"), "case.reopen")


@router.delete("/{case_id}", status_code=204, summary="Borrar un caso vacío (admin)")
def delete_case(case: ManageCase, session: DB, admin: Admin):
    for model in (Entity, Relation, Source, Account, AccountLink, Job, Finding):
        if session.execute(select(model.id).where(model.case_id == case.id).limit(1)).first() is not None:
            raise HTTPException(
                status_code=409,
                detail="El caso tiene datos: no se borra para conservar la trazabilidad. Archivalo.",
            )
    auditlog.record(session, "case.delete", user_id=admin.id, case_id=case.id, target=f"case:{case.id}",
                    detail={"name": case.name, "legal_basis": case.legal_basis})
    for section in session.execute(select(CaseSection).where(CaseSection.case_id == case.id)).scalars():
        session.delete(section)
    session.flush()
    session.delete(case)
    session.commit()
    return Response(status_code=204)


def _parse_event_date(props: dict) -> datetime | None:
    for key in _EVENT_DATE_KEYS:
        value = (props or {}).get(key)
        if not value:
            continue
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value, UTC)
            except (OverflowError, OSError, ValueError):
                continue
        try:
            return as_utc(datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")))
        except ValueError:
            try:
                from dateutil import parser as date_parser

                return as_utc(date_parser.parse(str(value)))
            except (ImportError, ValueError, OverflowError):
                continue
    return None


@router.get("/{case_id}/timeline", response_model=TimelineOut, summary="Línea de tiempo del caso")
def timeline(
    case: ReadCase, session: DB, user: CurrentUser, page: Paging,
    since: Annotated[datetime | None, Query(description="Desde (ISO 8601)")] = None,
    until: Annotated[datetime | None, Query(description="Hasta (ISO 8601)")] = None,
    kind: Annotated[list[Literal["post", "event"]] | None, Query(description="Qué incluir")] = None,
    account_id: Annotated[int | None, Query(description="Solo publicaciones de esta cuenta")] = None,
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
):
    """Publicaciones de las cuentas del caso y entidades `event` (no rechazadas), ordenadas por fecha.

    La fecha de un evento se toma de sus props (`date`, `datetime`, `timestamp`, `when`, `start`,
    `start_date`, `occurred_at` o `fecha`).
    """
    since, until = as_utc(since), as_utc(until)
    kinds = set(kind or ("post", "event"))
    items: list[TimelineItem] = []
    undated = 0
    if "post" in kinds:
        stmt = (
            select(Post, Account).join(Account, Post.account_id == Account.id)
            .where(Account.case_id == case.id, Post.created_at.is_not(None))
        )
        if account_id is not None:
            stmt = stmt.where(Account.id == account_id)
        if since is not None:
            stmt = stmt.where(Post.created_at >= since)
        if until is not None:
            stmt = stmt.where(Post.created_at <= until)
        for post, account in session.execute(stmt):
            items.append(TimelineItem(
                kind="post", at=post.created_at, title=f"{account.handle} ({account.platform})", text=post.text,
                post_id=post.id, account_id=account.id, entity_id=account.entity_id, platform=account.platform,
                handle=account.handle, post_kind=post.kind, source_id=account.source_id,
            ))
    if "event" in kinds and account_id is None:
        events = session.execute(
            select(Entity).where(Entity.case_id == case.id, Entity.type == "event", Entity.status != "rejected")
        ).scalars()
        for entity in events:
            at = _parse_event_date(entity.props)
            if at is None:
                undated += 1
                continue
            if (since is not None and at < since) or (until is not None and at > until):
                continue
            items.append(TimelineItem(
                kind="event", at=at, title=entity.label, text=str((entity.props or {}).get("description", "")),
                entity_id=entity.id, status=entity.status, source_id=entity.source_id,
            ))
    items.sort(key=lambda i: (i.at, i.kind, i.post_id or i.entity_id or 0), reverse=order == "desc")
    auditlog.record(session, "timeline.view", user_id=user.id, case_id=case.id, target=f"case:{case.id}")
    session.commit()
    return TimelineOut(
        items=items[page.offset: page.offset + page.limit], total=len(items),
        limit=page.limit, offset=page.offset, undated_events=undated,
    )


@router.get("/{case_id}/export", response_model=CaseExport, summary="Exportar el caso en JSON (queda auditado)")
def export_case(
    case: ReadCase, session: DB, user: CurrentUser,
    include_posts: Annotated[bool, Query(description="Incluir todas las publicaciones")] = False,
):
    def rows(model):
        return session.execute(select(model).where(model.case_id == case.id).order_by(model.id)).scalars().all()

    accounts = rows(Account)
    posts = []
    if include_posts and accounts:
        posts = session.execute(
            select(Post).where(Post.account_id.in_([a.id for a in accounts])).order_by(Post.account_id, Post.id)
        ).scalars().all()
    export = CaseExport(
        exported_at=datetime.now(UTC), case=CaseOut.model_validate(case),
        sources=[source_out(s) for s in rows(Source)],
        entities=[EntityOut.model_validate(e) for e in rows(Entity)],
        relations=[RelationOut.model_validate(r) for r in rows(Relation)],
        accounts=accounts_out(session, accounts),
        account_links=links_out(session, rows(AccountLink)),
        posts=[PostOut.model_validate(p) for p in posts],
    )
    auditlog.record(
        session, "case.export", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
        detail={"format": "json", "include_posts": include_posts, "entities": len(export.entities),
                "relations": len(export.relations), "accounts": len(export.accounts), "posts": len(export.posts)},
    )
    session.commit()
    return export


__all__ = ["CASE_STATUSES", "router"]
