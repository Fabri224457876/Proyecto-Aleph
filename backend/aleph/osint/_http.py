"""Utilidades HTTP compartidas por las herramientas que consultan red.

- Cliente inyectable: cada herramienta acepta un `httpx.AsyncClient`; si no se pasa, se crea uno
  con timeout y User-Agent propios.
- Manejo de límites de consulta: ante 429 (y 403 donde la API lo usa así) se reintenta respetando
  `Retry-After`, con tope de espera y de reintentos. Si no alcanza, se lanza RateLimitedError.
- `ensure_public_http_url`: impide que una herramienta que descarga una URL indicada por el analista
  alcance redes privadas, loopback o direcciones locales (protección SSRF). No protege contra DNS
  rebinding: el despliegue debe restringir la salida de red del worker.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .errors import (
    InvalidInputError,
    NetworkError,
    RateLimitedError,
    UnsafeTargetError,
    UpstreamError,
)

USER_AGENT = "Aleph-OSINT/0.1 (investigacion en fuentes abiertas; solo lectura)"
DEFAULT_TIMEOUT = 20.0
MAX_RETRIES = 2
MAX_RETRY_AFTER = 30.0

_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
_AMBIGUOUS_HOST = re.compile(r"[0-9.x]+", re.IGNORECASE)


@asynccontextmanager
async def open_client(client: httpx.AsyncClient | None) -> AsyncIterator[httpx.AsyncClient]:
    """Usa el cliente inyectado (sin cerrarlo) o crea uno propio con timeout por defecto."""
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}) as own:
        yield own


def _clean_url(url: str) -> str:
    """Quita query string y credenciales para mensajes de error y de procedencia."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname or ''}{parts.path}"


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:  # formato de fecha HTTP: se usa backoff exponencial
        return None


async def request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    rate_limit_statuses: tuple[int, ...] = (429,),
    follow_redirects: bool | None = None,
) -> httpx.Response:
    """Ejecuta la petición con reintentos ante límites de consulta y envoltura de errores de red."""
    send_headers = dict(headers or {})
    if "user-agent" not in {k.lower() for k in client.headers} and "User-Agent" not in send_headers:
        send_headers["User-Agent"] = USER_AGENT
    kwargs: dict[str, Any] = {}
    if follow_redirects is not None:
        kwargs["follow_redirects"] = follow_redirects
    attempt = 0
    while True:
        try:
            resp = await client.request(method, url, params=params, headers=send_headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise NetworkError(f"tiempo agotado al consultar {_clean_url(url)}") from exc
        except httpx.HTTPError as exc:
            raise NetworkError(f"error de red al consultar {_clean_url(url)}: {type(exc).__name__}") from exc
        if resp.status_code not in rate_limit_statuses:
            return resp
        retry_after = _retry_after_seconds(resp)
        wait = retry_after if retry_after is not None else float(2**attempt)
        if attempt >= MAX_RETRIES or wait > MAX_RETRY_AFTER:
            raise RateLimitedError(
                f"límite de consultas alcanzado en {_clean_url(url)} (HTTP {resp.status_code})",
                status_code=resp.status_code, url=_clean_url(url), retry_after=retry_after,
            )
        attempt += 1
        await asyncio.sleep(wait)


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    rate_limit_statuses: tuple[int, ...] = (429,),
) -> Any:
    """GET que devuelve el JSON decodificado o lanza UpstreamError."""
    resp = await request(
        client, "GET", url, params=params, headers=headers, rate_limit_statuses=rate_limit_statuses,
    )
    if resp.status_code != 200:
        raise UpstreamError(
            f"{_clean_url(url)} respondió HTTP {resp.status_code}",
            status_code=resp.status_code, url=_clean_url(url),
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise UpstreamError(f"{_clean_url(url)} no devolvió JSON válido", url=_clean_url(url)) from exc


def ensure_public_http_url(url: str, *, max_len: int = 2048) -> str:
    """Valida una URL http(s) pública. Lanza InvalidInputError o UnsafeTargetError."""
    raw = (url or "").strip()
    if not raw or len(raw) > max_len:
        raise InvalidInputError("URL vacía o demasiado larga")
    if any(ord(ch) < 33 or ord(ch) == 127 for ch in raw):
        raise InvalidInputError("la URL contiene espacios o caracteres de control")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidInputError("solo se admiten URLs http o https")
    if parts.username or parts.password:
        raise InvalidInputError("no se admiten URLs con credenciales")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidInputError("la URL no tiene host")
    try:
        parts.port  # noqa: B018 - valida el puerto (lanza ValueError si es inválido)
    except ValueError as exc:
        raise InvalidInputError("puerto inválido en la URL") from exc
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise UnsafeTargetError(f"host local no permitido: {host}")
    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is None and _AMBIGUOUS_HOST.fullmatch(host):
        # Formas numéricas que algunos resolutores interpretan como IP (p. ej. 2130706433)
        raise UnsafeTargetError(f"host numérico ambiguo no permitido: {host}")
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip is not None and not ip.is_global:
        raise UnsafeTargetError(f"dirección no pública no permitida: {host}")
    return raw


@dataclass(frozen=True)
class FetchedResource:
    url: str  # URL final tras las redirecciones validadas
    status_code: int
    content_type: str
    content: bytes
    truncated: bool


async def fetch_public(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    max_redirects: int = 3,
) -> FetchedResource:
    """Descarga una URL pública siguiendo redirecciones a mano, validando cada salto y con tope de bytes."""
    current = ensure_public_http_url(url)
    for _ in range(max_redirects + 1):
        try:
            async with client.stream(
                "GET", current, follow_redirects=False,
                headers={"User-Agent": USER_AGENT} if "user-agent" not in {k.lower() for k in client.headers} else None,
            ) as resp:
                if resp.status_code == 429:
                    raise RateLimitedError(
                        f"límite de consultas alcanzado en {_clean_url(current)}",
                        status_code=429, url=_clean_url(current),
                        retry_after=_retry_after_seconds(resp),
                    )
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    current = ensure_public_http_url(urljoin(current, resp.headers["location"]))
                    continue
                buffer = bytearray()
                truncated = False
                async for chunk in resp.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        del buffer[max_bytes:]
                        truncated = True
                        break
                return FetchedResource(
                    url=current, status_code=resp.status_code,
                    content_type=resp.headers.get("content-type", ""),
                    content=bytes(buffer), truncated=truncated,
                )
        except httpx.TimeoutException as exc:
            raise NetworkError(f"tiempo agotado al consultar {_clean_url(current)}") from exc
        except httpx.HTTPError as exc:
            raise NetworkError(f"error de red al consultar {_clean_url(current)}: {type(exc).__name__}") from exc
    raise UpstreamError(f"demasiadas redirecciones desde {_clean_url(url)}", url=_clean_url(url))
