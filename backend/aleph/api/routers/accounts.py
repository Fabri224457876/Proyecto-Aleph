"""Cuentas y publicaciones recolectadas, e ingreso de recolecciones aportadas por el operador."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import Query
from sqlalchemy import or_, select

from aleph.core.models import Account, Post
from aleph.core.schemas import CollectionResult

from .. import auditlog
from ..deps import DB, CurrentUser, Paging, ReadCase, SettingsDep, WriteCase, page_of, paginate
from ..presenters import accounts_out
from ..routing import make_router
from ..schemas import AccountOut, Page, PostOut
from ..services import IngestSummary, ingest_collection
from ..services.util import as_utc, get_in_case, like_pattern

router = make_router(prefix="/cases/{case_id}", tags=["cuentas"])


@router.get("/accounts", response_model=Page[AccountOut], summary="Listar cuentas del caso")
def list_accounts(
    case: ReadCase, session: DB, page: Paging,
    platform: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(description="Texto en handle, nombre o bio")] = None,
):
    stmt = select(Account).where(Account.case_id == case.id).order_by(Account.platform, Account.handle, Account.id)
    if platform:
        stmt = stmt.where(Account.platform == platform.strip().lower())
    if q and q.strip():
        pattern = like_pattern(q.strip())
        stmt = stmt.where(or_(
            Account.handle.ilike(pattern, escape="\\"), Account.display_name.ilike(pattern, escape="\\"),
            Account.bio.ilike(pattern, escape="\\"),
        ))
    items, total = paginate(session, stmt, page)
    return page_of(accounts_out(session, items), total, page)


@router.get("/accounts/{account_id}", response_model=AccountOut, summary="Ver cuenta")
def get_account(account_id: int, case: ReadCase, session: DB):
    account = get_in_case(session, Account, case.id, account_id, "La cuenta")
    return accounts_out(session, [account])[0]


@router.get("/accounts/{account_id}/posts", response_model=Page[PostOut],
            summary="Publicaciones de una cuenta (queda auditado)")
def list_posts(
    account_id: int, case: ReadCase, session: DB, user: CurrentUser, page: Paging,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    kind: Annotated[Literal["original", "reply", "repost", "quote"] | None, Query()] = None,
    q: Annotated[str | None, Query(description="Texto en la publicación")] = None,
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
):
    account = get_in_case(session, Account, case.id, account_id, "La cuenta")
    stmt = select(Post).where(Post.account_id == account.id)
    if since is not None:
        stmt = stmt.where(Post.created_at >= as_utc(since))
    if until is not None:
        stmt = stmt.where(Post.created_at <= as_utc(until))
    if kind:
        stmt = stmt.where(Post.kind == kind)
    if q and q.strip():
        stmt = stmt.where(Post.text.ilike(like_pattern(q.strip()), escape="\\"))
    if order == "asc":
        stmt = stmt.order_by(Post.created_at.asc(), Post.id.asc())
    else:
        stmt = stmt.order_by(Post.created_at.desc(), Post.id.desc())
    items, total = paginate(session, stmt, page)
    auditlog.record(session, "account.posts.view", user_id=user.id, case_id=case.id,
                    target=f"account:{account.id}", detail={"offset": page.offset, "limit": page.limit})
    session.commit()
    return page_of(items, total, page)


@router.post("/collections", response_model=IngestSummary, status_code=201,
             summary="Ingresar una recolección ya normalizada (CollectionResult)")
def ingest(body: CollectionResult, case: WriteCase, session: DB, user: CurrentUser, settings: SettingsDep):
    """Persiste de forma idempotente lo que devolvió un conector o una importación del operador.

    Es el mismo servicio que usan los workers (`aleph.api.services.ingest_collection`).
    """
    summary = ingest_collection(session, case.id, body, user.id, data_dir=settings.data_dir)
    session.commit()
    return summary
