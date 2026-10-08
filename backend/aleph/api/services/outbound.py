"""Puntos de inyección de la capa de integración: clientes HTTP salientes y proveedores.

En producción devuelven None y cada motor crea su propio cliente. Los tests los reemplazan
(`app.dependency_overrides`) por un `httpx.AsyncClient` con `httpx.MockTransport`, así la suite
corre sin red. No importan FastAPI: las rutas los declaran con `Depends`.
"""

from __future__ import annotations

from typing import Any

import httpx


def outbound_http() -> httpx.AsyncClient | None:
    """Cliente para conectores y herramientas OSINT. None = cada uno usa el suyo."""
    return None


def llm_http() -> httpx.AsyncClient | None:
    """Transporte del endpoint de FUNES. None = FunesClient crea el suyo (producción)."""
    return None


def enrichment_providers() -> list[Any] | None:
    """Instancias de los proveedores de enriquecimiento. None = los por defecto, con su cliente."""
    return None
