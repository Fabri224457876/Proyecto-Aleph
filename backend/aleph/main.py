from contextlib import asynccontextmanager

from fastapi import FastAPI

from aleph.api import configure_app, router
from aleph.core.config import get_settings
from aleph.core.db import init_db

_DEV_SECRET = "dev-only-change-me"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.env != "dev" and settings.secret_key == _DEV_SECRET:
        raise RuntimeError("ALEPH_SECRET_KEY no está configurada: no se arranca fuera de desarrollo sin clave propia")

    app = FastAPI(title="Aleph · P.R.O.A.", version="0.1.0", lifespan=lifespan)
    configure_app(app)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(router, prefix="/api")
    return app


app = create_app()
