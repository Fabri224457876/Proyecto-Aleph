"""Miembros de un caso: quién tiene acceso (need-to-know).

Ver un caso y gestionar sus miembros son cosas distintas. El auditor ve todos los casos pero no los
gestiona: agregar o quitar miembros lo hace un admin o un analista miembro del caso. Un analista que
no es miembro recibe 404, igual que si el caso no existiera.
"""

from fastapi import HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from aleph.core.models import CaseMember, User

from .. import auditlog
from ..deps import DB, CurrentUser, ManageMembers, Paging, ReadCase, Writer, page_of, paginate
from ..errors import Conflict
from ..routing import make_router
from ..schemas import CaseMemberOut, MemberAddIn, Message, Page

router = make_router(prefix="/cases/{case_id}/members", tags=["miembros del caso"])


def _out(member: CaseMember, username: str, role: str) -> CaseMemberOut:
    return CaseMemberOut(
        user_id=member.user_id, username=username, role=role, added_by=member.added_by, added_at=member.added_at,
    )


def _membership(session, case_id: int, user_id: int) -> CaseMember | None:
    return session.execute(
        select(CaseMember).where(CaseMember.case_id == case_id, CaseMember.user_id == user_id)
    ).scalars().first()


def _target_user(session, body: MemberAddIn) -> User:
    if body.user_id is not None:
        user = session.get(User, body.user_id)
        ref = str(body.user_id)
    else:
        name = body.username.strip()
        user = session.execute(select(User).where(func.lower(User.username) == name.lower())).scalars().first()
        ref = f"'{name}'"
    if user is None:
        raise HTTPException(status_code=404, detail=f"El usuario {ref} no existe.")
    return user


@router.get("", response_model=Page[CaseMemberOut], summary="Miembros del caso (quién tiene acceso)")
def list_members(case: ReadCase, session: DB, user: CurrentUser, page: Paging):
    stmt = (
        select(CaseMember, User.username, User.role)
        .join(User, CaseMember.user_id == User.id)
        .where(CaseMember.case_id == case.id)
        .order_by(CaseMember.id)
    )
    rows, total = paginate(session, stmt, page, scalars=False)
    items = [_out(member, username, role) for member, username, role in rows]
    auditlog.record(session, "case.members.view", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
                    detail={"total": total})
    session.commit()
    return page_of(items, total, page)


@router.post(
    "", response_model=CaseMemberOut, status_code=201,
    summary="Agregar un analista al caso, por id o por nombre (admin o miembro)",
    responses={409: {"model": Message, "description": "Ya es miembro, no es analista o está desactivado"}},
)
def add_member(body: MemberAddIn, case: ManageMembers, session: DB, actor: Writer):
    target = _target_user(session, body)
    if not target.active:
        raise Conflict("El usuario está desactivado.")
    # Admin y auditor ven todos los casos: agregarlos como miembros no cambiaría nada
    if target.role != "analyst":
        raise Conflict("Solo se agregan analistas: admin y auditor ya ven todos los casos.")
    if _membership(session, case.id, target.id) is not None:
        raise Conflict("El usuario ya es miembro de este caso.")
    member = CaseMember(case_id=case.id, user_id=target.id, added_by=actor.id)
    session.add(member)
    try:
        session.flush()
    except IntegrityError:  # otro pedido lo agregó entre la consulta y la escritura
        session.rollback()
        raise Conflict("El usuario ya es miembro de este caso.") from None
    auditlog.record(
        session, "case.member.add", user_id=actor.id, case_id=case.id, target=f"user:{target.id}",
        detail={"user_id": target.id, "username": target.username, "role": target.role},
    )
    session.commit()
    return _out(member, target.username, target.role)


@router.delete(
    "/{user_id}", status_code=204, summary="Quitar un miembro del caso (admin o miembro)",
    responses={
        404: {"model": Message, "description": "Ese usuario no es miembro del caso"},
        409: {"model": Message, "description": "Quedaría el caso sin ningún analista miembro"},
    },
)
def remove_member(user_id: int, case: ManageMembers, session: DB, actor: Writer):
    member = _membership(session, case.id, user_id)
    if member is None:
        raise HTTPException(status_code=404, detail=f"El usuario {user_id} no es miembro de este caso.")
    # Sin analista miembro, nadie puede trabajar el caso: solo un admin puede dejarlo así
    if actor.role != "admin":
        remaining_analysts = session.scalar(
            select(func.count(CaseMember.id))
            .join(User, CaseMember.user_id == User.id)
            .where(CaseMember.case_id == case.id, CaseMember.user_id != user_id, User.role == "analyst")
        ) or 0
        if remaining_analysts == 0:
            raise Conflict(
                "No podés quitar al último analista miembro: el caso quedaría sin nadie que lo trabaje. "
                "Solo un admin puede hacerlo."
            )
    target = session.get(User, user_id)
    session.delete(member)
    session.flush()
    auditlog.record(
        session, "case.member.remove", user_id=actor.id, case_id=case.id, target=f"user:{user_id}",
        detail={"user_id": user_id, "username": target.username, "role": target.role},
    )
    session.commit()
    return Response(status_code=204)
