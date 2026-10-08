"""Inicio de sesión y gestión de usuarios."""

from fastapi import HTTPException
from sqlalchemy import func, select

from aleph.core.models import User

from .. import auditlog, security
from ..deps import DB, Admin, CurrentUser, Paging, SettingsDep, page_of, paginate
from ..routing import make_router
from ..schemas import LoginIn, Page, PasswordChange, TokenOut, UserCreate, UserOut, UserUpdate

router = make_router()
auth = make_router(prefix="/auth", tags=["auth"])
users = make_router(prefix="/users", tags=["usuarios"])


@auth.post("/login", response_model=TokenOut, summary="Iniciar sesión")
def login(body: LoginIn, session: DB, settings: SettingsDep):
    username = body.username.strip().lower()
    user = session.execute(select(User).where(func.lower(User.username) == username)).scalars().first()
    if user is None:
        security.burn_verification(body.password)
        ok = False
    else:
        ok = security.verify_password(user.password_hash, body.password) and user.active
    if not ok:
        auditlog.record(
            session, "auth.login.failed", user_id=user.id if user else None,
            target=f"user:{username[:64]}", detail={"username": username[:64]},
        )
        session.commit()
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(body.password)
    token, expires_in = security.create_token(user.id, user.role, settings)
    auditlog.record(session, "auth.login", user_id=user.id, target=f"user:{user.username}")
    session.commit()
    return TokenOut(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))


@auth.get("/me", response_model=UserOut, summary="Usuario actual")
def me(user: CurrentUser):
    return user


@auth.post("/password", response_model=UserOut, summary="Cambiar la contraseña propia")
def change_password(body: PasswordChange, session: DB, user: CurrentUser):
    if not security.verify_password(user.password_hash, body.current_password):
        auditlog.record(session, "auth.password.failed", user_id=user.id, target=f"user:{user.username}")
        session.commit()
        raise HTTPException(status_code=403, detail="La contraseña actual no es correcta.")
    user.password_hash = security.hash_password(body.new_password)
    auditlog.record(session, "auth.password.change", user_id=user.id, target=f"user:{user.username}")
    session.commit()
    return user


@users.get("", response_model=Page[UserOut], summary="Listar usuarios")
def list_users(session: DB, admin: Admin, page: Paging):
    items, total = paginate(session, select(User).order_by(User.id), page)
    return page_of(items, total, page)


@users.post("", response_model=UserOut, status_code=201, summary="Crear usuario")
def create_user(body: UserCreate, session: DB, admin: Admin):
    if session.execute(select(User.id).where(func.lower(User.username) == body.username)).first():
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese nombre.")
    user = User(username=body.username, password_hash=security.hash_password(body.password), role=body.role)
    session.add(user)
    session.flush()
    auditlog.record(
        session, "user.create", user_id=admin.id, target=f"user:{user.username}",
        detail={"id": user.id, "role": user.role},
    )
    session.commit()
    return user


@users.patch("/{user_id}", response_model=UserOut, summary="Cambiar rol, estado o contraseña de un usuario")
def update_user(user_id: int, body: UserUpdate, session: DB, admin: Admin):
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"El usuario {user_id} no existe.")
    changes: dict = {}
    if body.role is not None and body.role != user.role:
        changes["role"] = {"from": user.role, "to": body.role}
    if body.active is not None and body.active != user.active:
        changes["active"] = {"from": user.active, "to": body.active}
    if user.id == admin.id and changes:
        raise HTTPException(status_code=409, detail="No podés cambiar tu propio rol ni desactivarte.")
    if "role" in changes:
        user.role = body.role
    if "active" in changes:
        user.active = body.active
    if body.password is not None:
        user.password_hash = security.hash_password(body.password)
        changes["password"] = "cambiada"
    if changes:
        auditlog.record(session, "user.update", user_id=admin.id, target=f"user:{user.username}",
                        detail={"id": user.id, "changes": changes})
        session.commit()
    return user


router.include_router(auth)
router.include_router(users)
