"""Estado de la base de demostración: cuántos administradores tiene.

Lo usan `scripts/dev.ps1` y `scripts/dev.sh` para decidir si corren el bootstrap:

    python -m aleph.demo.estado   # imprime 0 si no hay tabla de usuarios ni administradores
"""

from __future__ import annotations

from sqlalchemy import func, inspect, select

from aleph.core.config import get_settings
from aleph.core.db import make_engine
from aleph.core.models import User


def count_admins(url: str | None = None) -> int:
    engine = make_engine(url or get_settings().database_url)
    try:
        if not inspect(engine).has_table(User.__tablename__):
            return 0
        with engine.connect() as connection:
            total = connection.execute(select(func.count(User.id)).where(User.role == "admin")).scalar()
        return int(total or 0)
    finally:
        engine.dispose()


if __name__ == "__main__":
    print(count_admins())
