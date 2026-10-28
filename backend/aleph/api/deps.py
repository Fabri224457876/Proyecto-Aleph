"""Dependencias de FastAPI: sesión, usuario actual, roles, acceso por caso y paginación."""

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aleph.core.config import Settings, get_settings
from aleph.core.db import get_session
from aleph.core.models import Case, CaseMember, User

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


# ---------------------------------------------------------------- acceso por caso (need-to-know)
#
# Único punto de control: todo endpoint bajo /cases/{case_id} recibe el caso por ReadCase,
# WriteCase, ManageCase o ManageMembers, y todos pasan por `_load_case`. Admin y auditor ven
# todos los casos; un analista ve solo aquellos donde es miembro. Para un analista que no es
# miembro, el caso responde igual que si no existiera (mismo 404).

SEES_ALL_CASES = AUDITORS


def sees_all_cases(user: User) -> bool:
    return user.role in SEES_ALL_CASES


def can_see_case(session: Session, case_id: int | None, user: User) -> bool:
    """Si el usuario ve el caso. Sin caso (`None`), solo lo ven admin y auditor."""
    if sees_all_cases(user):
        return True
    if case_id is None:
        return False
    return session.execute(
        select(CaseMember.id).where(CaseMember.case_id == case_id, CaseMember.user_id == user.id)
    ).first() is not None


def scope_to_user(stmt, user: User, case_column):
    """Limita una consulta a los casos que el usuario ve. Sirve para listados y conteos."""
    if sees_all_cases(user):
        return stmt
    return stmt.where(case_column.in_(select(CaseMember.case_id).where(CaseMember.user_id == user.id)))


def _load_case(session: Session, case_id: int, user: User) -> Case:
    case = session.get(Case, case_id)
    if case is None or not can_see_case(session, case_id, user):
        raise HTTPException(status_code=404, detail=f"El caso {case_id} no existe.")
    return case


def readable_case(case_id: int, session: DB, user: AnyUser) -> Case:
    return _load_case(session, case_id, user)


def managed_case(case_id: int, session: DB, user: Writer) -> Case:
    """Caso para un rol que escribe, sin importar su estado (cerrar, archivar, reabrir)."""
    return _load_case(session, case_id, user)


def writable_case(case_id: int, session: DB, user: Writer) -> Case:
    case = _load_case(session, case_id, user)
    if case.status != "open":
        raise HTTPException(
            status_code=409, detail=f"El caso está {_STATUS_ES.get(case.status, case.status)}: es de solo lectura."
        )
    return case


_STATUS_ES = {"closed": "cerrado", "archived": "archivado"}

ReadCase = Annotated[Case, Depends(readable_case)]
WriteCase = Annotated[Case, Depends(writable_case)]
ManageCase = Annotated[Case, Depends(managed_case)]


def members_case(case: ReadCase, user: Writer) -> Case:
    """Caso para gestionar quién tiene acceso: admin o analista miembro. El auditor lo ve, no lo gestiona.

    El orden importa: primero el caso (404 para un analista que no es miembro), después el rol.
    """
    return case


ManageMembers = Annotated[Case, Depends(members_case)]


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
