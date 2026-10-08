"""Interfaz de conectores. Contrato compartido: lo edita solo el orquestador.

Un conector recolecta de una fuente y devuelve un CollectionResult normalizado.
No toca la base de datos. Solo usa APIs públicas/oficiales o archivos que aporta el operador.
"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

import httpx

from aleph.core.schemas import CollectionResult


class ConnectorError(Exception):
    pass


class Connector(ABC):
    name: ClassVar[str]  # identificador único, p. ej. "bluesky"
    title: ClassVar[str]  # nombre visible en español
    mode: ClassVar[Literal["live", "import"]]
    # Parámetros que acepta, para armar el formulario: {nombre: descripción}
    params: ClassVar[dict[str, str]] = {}
    # Nombres de Settings que necesita (p. ej. ["x_bearer_token"]); vacío si no requiere clave
    requires: ClassVar[list[str]] = []

    def __init__(self, client: httpx.AsyncClient | None = None, settings: Any = None):
        self.client = client
        self.settings = settings

    @abstractmethod
    async def collect(self, **params: Any) -> CollectionResult:
        """Conectores 'live': params según `self.params`. Conectores 'import': reciben `path` o `data`."""


_REGISTRY: dict[str, type[Connector]] = {}


def register(cls: type[Connector]) -> type[Connector]:
    _REGISTRY[cls.name] = cls
    return cls


def get_connector(name: str) -> type[Connector]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ConnectorError(f"Conector desconocido: {name}") from None


def list_connectors() -> list[type[Connector]]:
    return sorted(_REGISTRY.values(), key=lambda c: c.name)
