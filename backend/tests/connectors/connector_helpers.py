"""Ayudantes compartidos por los tests de conectores. No hacen ninguna consulta de red."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from aleph.connectors import _util

FIXTURES = Path(__file__).parent / "fixtures"

# Settings sin claves: los tests que no usan token deben pasar esto para no leer el entorno.
NO_KEYS = SimpleNamespace(github_token="", x_bearer_token="")


def client_for(handler: Callable[[httpx.Request], Any]) -> httpx.AsyncClient:
    """Cliente httpx que responde con `handler` (sincrónico o asincrónico). Sin red."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def json_response(
    data: Any, status: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, json=data, headers=headers or {})


def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Reemplaza las esperas ante 429 por un registro. Devuelve la lista de esperas pedidas."""
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(_util, "_sleep", fake_sleep)
    return waits
