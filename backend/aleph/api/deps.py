"""Dependencias de FastAPI: sesión, usuario actual, roles, caso y paginación."""

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aleph.core.config import Settings, get_settings
from aleph.core.db import get_session
from aleph.core.models import Case, User

from . import auditlog
from .schemas import ROLES
from .security import TokenError, decode_token

WRITERS = ("admin", "analyst")
AUDITORS = ("admin", "auditor")

_bearer = HTTPBearer(auto_error=False, description="Token devuelto por POST /api/auth/login")


def db(session: Annotated[Session, Depends(get_session)]) -> Session:
    """Sesión del pedido, con las escrituras serializadas respecto de la cadena de auditoría."""
    return auditlog.guard(session)


DB = Annotated[Session, Depends(db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(status_code=401, detail=message, headers={"WWW-Authenticate": "Bearer"})


def current_user(
    session: DB,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise _unauthorized("Falta el token de acceso.")
    try:
        payload = decode_token(credentials.credentials, settings)
        user_id = int(payload["sub"])
    except (TokenError, ValueError, KeyError):
        raise _unauthorized("El token no es válido o está vencido.") from None
    user = session.get(User, user_id)
    if user is None or not user.active or user.role not in ROLES:
        raise _unauthorized("El usuario no existe o está desactivado.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: str):
    """Dependencia que exige uno de los roles dados. Los intentos denegados quedan auditados."""

    def dependency(request: Request, session: DB, user: CurrentUser) -> User:
        if user.role not in roles:
            auditlog.record(
                session, "auth.denied", user_id=user.id, target=request.url.path,
                detail={"method": request.method, "role": user.role, "required": list(roles)},
            )
            session.commit()
            raise HTTPException(status_code=403, detail="Tu rol no permite esta acción.")
        return user

    return dependency


AnyUser = Annotated[User, Depends(require_role(*ROLES))]
Writer = Annotated[User, Depends(require_role(*WRITERS))]
Admin = Annotated[User, Depends(require_role("admin"))]
Auditor = Annotated[User, Depends(require_role(*AUDITORS))]


def _load_case(session: Session, case_id: int) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"El caso {case_id} no existe.")
    return case


def readable_case(case_id: int, session: DB, user: AnyUser) -> Case:
    return _load_case(session, case_id)


def managed_case(case_id: int, session: DB, user: Writer) -> Case:
    """Caso para un rol que escribe, sin importar su estado (cerrar, archivar, reabrir)."""
    return _load_case(session, case_id)


def writable_case(case_id: int, session: DB, user: Writer) -> Case:
    case = _load_case(session, case_id)
    if case.status != "open":
        raise HTTPException(
            status_code=409, detail=f"El caso está {_STATUS_ES.get(case.status, case.status)}: es de solo lectura."
        )
    return case


_STATUS_ES = {"closed": "cerrado", "archived": "archivado"}

ReadCase = Annotated[Case, Depends(readable_case)]
WriteCase = Annotated[Case, Depends(writable_case)]
ManageCase = Annotated[Case, Depends(managed_case)]


class PageParams:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=500, description="Tamaño de página")] = 50,
        offset: Annotated[int, Query(ge=0, description="Desplazamiento desde el primer resultado")] = 0,
    ):
        self.limit = limit
        self.offset = offset


Paging = Annotated[PageParams, Depends()]


def paginate(session: Session, stmt, page: PageParams, *, scalars: bool = True):
    """Devuelve (filas de la página, total sin paginar)."""
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    result = session.execute(stmt.limit(page.limit).offset(page.offset))
    return (result.scalars().all() if scalars else result.all()), total


def page_of(items, total: int, page: PageParams) -> dict:
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}
