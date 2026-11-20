from contextlib import asynccontextmanager

from fastapi import FastAPI

from aleph.api import configure_app, router
from aleph.api.services import tlon as tlon_service
from aleph.core.config import get_settings
from aleph.core.db import SessionLocal, init_db

_DEV_SECRET = "dev-only-change-me"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as session:
        # Un trabajo en curso murió con el proceso anterior: se marca fallido en vez de quedar «corriendo» para siempre
        if tlon_service.fail_orphans(session):
            session.commit()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.env != "dev" and (settings.secret_key == _DEV_SECRET or len(settings.secret_key) < 32):
        raise RuntimeError("ALEPH_SECRET_KEY no está configurada (o tiene menos de 32 caracteres): "
                           "no se arranca fuera de desarrollo sin clave propia")

    app = FastAPI(title="Aleph · P.R.O.A.", version="0.1.0", lifespan=lifespan)
    configure_app(app)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(router, prefix="/api")
    return app


app = create_app()
