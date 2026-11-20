from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _unicode_lower(value):
    return value.lower() if isinstance(value, str) else value


def _sqlite_connect(dbapi_conn, _record) -> None:
    """Acerca SQLite al comportamiento de PostgreSQL en dos puntos donde difieren.

    - Claves foráneas: SQLite no las exige salvo que se le pida. Así, un borrado que deja filas
      huérfanas falla en desarrollo y en las pruebas igual que en producción.
    - `lower()`: el de SQLite solo pasa a minúscula letras ASCII («ÑANDÚ» queda igual), y la
      deduplicación compara `lower(columna)` con `valor.lower()` de Python. Se reemplaza por el
      de Python, que es el que usa también `ilike`.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    dbapi_conn.create_function("lower", 1, _unicode_lower, deterministic=True)


def make_engine(url: str | None = None):
    url = url or get_settings().database_url
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)
        event.listen(engine, "connect", _sqlite_connect)
        return engine
    return create_engine(url, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(bind=None) -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind or engine)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
