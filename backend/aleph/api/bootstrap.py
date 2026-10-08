"""Crea el primer administrador.

    python -m aleph.api.bootstrap --username ana
    (la contraseña se toma de ALEPH_ADMIN_PASSWORD, de --password, o se pide por teclado)

No hay usuario ni contraseña por defecto: si falta alguno, el comando termina con error.
"""

import argparse
import getpass
import os
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aleph.core.models import User

from . import auditlog, security
from .schemas import _password, _username


class BootstrapError(Exception):
    pass


def create_admin(session: Session, username: str, password: str) -> User:
    """Crea un usuario admin y lo deja auditado. No hace commit."""
    try:
        username = _username(username or "")
        password = _password(password or "")
    except ValueError as exc:
        raise BootstrapError(str(exc)) from exc
    if session.execute(select(User.id).where(func.lower(User.username) == username)).first():
        raise BootstrapError(f"El usuario '{username}' ya existe.")
    user = User(username=username, password_hash=security.hash_password(password), role="admin")
    session.add(user)
    session.flush()
    auditlog.record(session, "user.bootstrap", user_id=user.id, target=f"user:{username}",
                    detail={"id": user.id, "role": "admin"})
    return user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aleph.api.bootstrap", description="Crea el primer administrador de Aleph."
    )
    parser.add_argument("--username", default=os.environ.get("ALEPH_ADMIN_USERNAME", ""),
                        help="Nombre de usuario (o variable ALEPH_ADMIN_USERNAME)")
    parser.add_argument("--password", default=None,
                        help="Contraseña (mejor la variable ALEPH_ADMIN_PASSWORD: los argumentos quedan en el historial)")
    args = parser.parse_args(argv)

    password = args.password or os.environ.get("ALEPH_ADMIN_PASSWORD", "")
    if not args.username:
        print("Falta el usuario: pasá --username o definí ALEPH_ADMIN_USERNAME.", file=sys.stderr)
        return 2
    if not password and sys.stdin.isatty():
        password = getpass.getpass("Contraseña del administrador: ")
        if password != getpass.getpass("Repetila: "):
            print("Las contraseñas no coinciden.", file=sys.stderr)
            return 2
    if not password:
        print("Falta la contraseña: definí ALEPH_ADMIN_PASSWORD o pasá --password.", file=sys.stderr)
        return 2

    from aleph.core.db import SessionLocal, init_db

    init_db()
    with SessionLocal() as session:
        try:
            user = create_admin(session, args.username, password)
            session.commit()
        except BootstrapError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    print(f"Administrador '{user.username}' creado (id {user.id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
