"""Base de datos de las pruebas.

Por defecto, SQLite (en memoria o en el archivo indicado). Con ALEPH_TEST_DATABASE_URL apuntando a un
PostgreSQL vacío y descartable, las mismas pruebas corren contra PostgreSQL: cada base nueva borra y
vuelve a crear el esquema completo.
"""

import os

from sqlalchemy import text

from aleph.core.db import Base, init_db, make_engine

TEST_DATABASE_URL = os.environ.get("ALEPH_TEST_DATABASE_URL", "")


def fresh_engine(sqlite_url: str = "sqlite:///:memory:"):
    if TEST_DATABASE_URL:
        engine = make_engine(TEST_DATABASE_URL)
        # Una prueba anterior pudo dejar una sesión abierta; en una base descartable se corta.
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                              "WHERE datname = current_database() AND pid <> pg_backend_pid()"))
        Base.metadata.drop_all(engine)
    else:
        engine = make_engine(sqlite_url)
    init_db(engine)
    return engine
