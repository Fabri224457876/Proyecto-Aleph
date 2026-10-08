"""API del núcleo P.R.O.A. (WP2).

`router` se resuelve de forma perezosa para que `aleph.api.services` (que usan los workers)
se pueda importar sin cargar FastAPI.
"""

import os

__all__ = ["DEFAULT_CORS_ORIGIN_REGEX", "configure_app", "router"]

# La extensión Aleph Lens llama desde chrome-extension://<id>; el frontend de desarrollo, desde localhost
DEFAULT_CORS_ORIGIN_REGEX = r"^(chrome-extension://[a-z]{32}|https?://(localhost|127\.0\.0\.1)(:\d{1,5})?)$"


def configure_app(app, *, origins: list[str] | None = None, origin_regex: str | None = None):
    """Agrega el middleware CORS a la app. Llamar desde `aleph.main.create_app()`.

    Por defecto solo admite extensiones de Chrome (por regex) y localhost. Se configura con
    `ALEPH_CORS_ORIGINS` (orígenes exactos separados por comas) y `ALEPH_CORS_ORIGIN_REGEX`,
    o pasando los argumentos. La API se autentica con `Authorization: Bearer`, no con cookies:
    no se habilitan credenciales.
    """
    from starlette.middleware.cors import CORSMiddleware

    if origins is None:
        origins = [o.strip() for o in os.environ.get("ALEPH_CORS_ORIGINS", "").split(",") if o.strip()]
    if origin_regex is None:
        origin_regex = os.environ.get("ALEPH_CORS_ORIGIN_REGEX", DEFAULT_CORS_ORIGIN_REGEX)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=origin_regex or None,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Aleph-SHA256", "Content-Disposition"],
        max_age=600,
    )
    return app


def __getattr__(name: str):
    if name == "router":
        from .routers import router

        globals()["router"] = router
        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
