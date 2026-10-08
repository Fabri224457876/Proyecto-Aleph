"""Ayudantes comunes de los conectores. No es API pública: los conectores los importan desde aquí.

- HttpSession: cliente HTTP con timeouts y espera ante 429 con tope (RateLimited).
- Normalización: fechas en UTC, handles, menciones, hashtags y URLs desde texto.
- HTML: DOM mínimo sobre html.parser, para páginas (Telegram) y contenido (Mastodon, X).
- Exportaciones: lectura de archivos window.YTD y corrección de mojibake latin-1/UTF-8.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Self

import httpx
from dateutil import parser as dateutil_parser

from aleph.connectors.base import ConnectorError
from aleph.core.schemas import CollectionResult

# Las cabeceras HTTP sólo admiten ASCII: nada de tildes ni eñes aquí.
USER_AGENT = "Aleph-OSINT/0.1 (OSINT/CTI research; contacto: operador de la instancia)"
DEFAULT_TIMEOUT = 20.0
DEFAULT_BACKOFF = 5.0  # espera si el servidor no indica cuánto debe esperarse
MAX_RETRY_AFTER = 30.0  # tope de espera ante 429; por encima se devuelve lo obtenido
MAX_RETRIES = 2
KINDS = ("original", "reply", "repost", "quote")


# --------------------------------------------------------------------------- errores y espera


class RateLimited(ConnectorError):
    """La API pidió esperar más que el tope. Los conectores la capturan y devuelven lo obtenido."""

    def __init__(self, wait: float, where: str) -> None:
        self.wait = wait
        self.where = where
        super().__init__(f"límite de tasa en {where} (espera pedida: {wait:.0f} s)")


def rate_limit_warning(exc: RateLimited) -> str:
    return (
        f"Límite de tasa alcanzado en {exc.where} (la API pidió esperar {exc.wait:.0f} s, "
        "por encima del tope). Se devuelve lo obtenido hasta ese punto."
    )


async def _sleep(seconds: float) -> None:
    """Único punto de espera. Los tests lo reemplazan para no esperar de verdad."""
    await asyncio.sleep(seconds)


def utcnow() -> datetime:
    return datetime.now(UTC)


def empty_result(
    connector: str, reference: str, warnings: list[str], raw: Any = None
) -> CollectionResult:
    return CollectionResult(
        connector=connector,
        reference=reference,
        retrieved_at=utcnow(),
        warnings=warnings,
        raw=raw,
    )


# --------------------------------------------------------------------------- HTTP


def retry_after_seconds(response: httpx.Response, default: float = DEFAULT_BACKOFF) -> float:
    """Segundos a esperar según las cabeceras de la respuesta.

    Prueba Retry-After (segundos o fecha HTTP) y después las cabeceras de reinicio de cuota
    (epoch en segundos, segundos relativos o fecha ISO), según la convención de cada API.
    """
    now = time.time()
    value = response.headers.get("retry-after")
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(value).timestamp() - now)
            except (TypeError, ValueError):
                pass
    for name in ("x-ratelimit-reset", "x-rate-limit-reset", "ratelimit-reset"):
        raw = response.headers.get(name)
        if not raw:
            continue
        try:
            reset = float(raw)
        except ValueError:
            parsed = parse_datetime(raw)
            if parsed is None:
                continue
            reset = parsed.timestamp()
        if reset > 1_000_000_000:  # epoch
            return max(0.0, reset - now)
        return max(0.0, reset)  # segundos relativos
    return default


def _where(url: str) -> str:
    """Host y ruta, sin query string: para mensajes de error sin datos sensibles."""
    parsed = httpx.URL(str(url))
    return f"{parsed.host}{parsed.path}"


def _check_status(response: httpx.Response) -> None:
    code = response.status_code
    if code < 400:
        return
    where = _where(str(response.url))
    if code in (401, 403):
        raise ConnectorError(
            f"acceso denegado por {where} (HTTP {code}); revisá credenciales o permisos"
        )
    if code == 404:
        raise ConnectorError(f"recurso no encontrado en {where} (HTTP 404)")
    if code >= 500:
        raise ConnectorError(f"error del servidor en {where} (HTTP {code})")
    raise ConnectorError(f"{where} respondió HTTP {code}")


def _json_or_error(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ConnectorError(f"la respuesta de {_where(str(response.url))} no es JSON") from exc


def _is_missing(response: httpx.Response, missing: Callable[[httpx.Response], bool] | None) -> bool:
    return response.status_code == 404 or (missing is not None and missing(response))


class HttpSession:
    """Cliente HTTP de un conector. Uso: `async with HttpSession(self.client) as http: ...`.

    - Usa el httpx.AsyncClient inyectado; si no hay, crea uno propio y lo cierra al salir.
    - Timeout en todas las llamadas.
    - Ante 429 (o si `rate_hook` pide espera) espera según la cabecera y reintenta. Si la espera
      supera `max_retry_after` o se agotan los reintentos, lanza RateLimited.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        max_retry_after: float = MAX_RETRY_AFTER,
        rate_hook: Callable[[httpx.Response], float | None] | None = None,
    ) -> None:
        self._injected = client
        self._client: httpx.AsyncClient | None = None
        self._owned = False
        self._headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_retry_after = max_retry_after
        self._rate_hook = rate_hook

    async def __aenter__(self) -> Self:
        if self._injected is not None:
            self._client = self._injected
        else:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
            self._owned = True
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()
        self._client = None

    def _wait_needed(self, response: httpx.Response) -> float | None:
        if self._rate_hook is not None:
            wait = self._rate_hook(response)
            if wait is not None:
                return wait
        if response.status_code == 429:
            return retry_after_seconds(response)
        return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("HttpSession debe usarse dentro de 'async with'")
        where = _where(url)
        merged = {**self._headers, **(headers or {})}
        attempts = 0
        while True:
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    headers=merged,
                    timeout=self._timeout,
                    follow_redirects=follow_redirects,
                )
            except httpx.TimeoutException as exc:
                raise ConnectorError(f"tiempo de espera agotado consultando {where}") from exc
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    f"error de red consultando {where} ({type(exc).__name__})"
                ) from exc
            wait = self._wait_needed(response)
            if wait is None:
                return response
            attempts += 1
            if attempts > self._max_retries or wait > self._max_retry_after:
                raise RateLimited(wait, where)
            await _sleep(wait)

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        missing: Callable[[httpx.Response], bool] | None = None,
    ) -> Any | None:
        """JSON de la respuesta. Devuelve None si el recurso no existe (404 o `missing`)."""
        response = await self.request("GET", url, params=params, headers=headers)
        if _is_missing(response, missing):
            return None
        _check_status(response)
        return _json_or_error(response)

    async def get_json_with_next(
        self, url: str, *, params: dict[str, Any] | None = None
    ) -> tuple[Any, str | None]:
        """JSON y URL de la página siguiente según la cabecera Link (rel="next")."""
        response = await self.request("GET", url, params=params)
        _check_status(response)
        next_link = response.links.get("next") or {}
        return _json_or_error(response), next_link.get("url")

    async def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        missing: Callable[[httpx.Response], bool] | None = None,
    ) -> str | None:
        """Texto de la respuesta. Devuelve None si el recurso no existe (404 o `missing`)."""
        response = await self.request("GET", url, params=params, headers=headers)
        if _is_missing(response, missing):
            return None
        _check_status(response)
        return response.text


# --------------------------------------------------------------------------- parámetros y ajustes


def require_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConnectorError(f"falta el parámetro '{name}'")
    return text


def positive_int(value: Any, name: str, default: int, maximum: int | None = None) -> int:
    if value is None:
        number = default
    else:
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ConnectorError(f"'{name}' debe ser un entero") from None
    if number < 1:
        raise ConnectorError(f"'{name}' debe ser mayor que 0")
    return min(number, maximum) if maximum else number


def get_setting(settings: Any, name: str) -> str:
    """Valor de una clave de configuración. Si no hay objeto de settings, usa aleph.core.config."""
    if settings is None:
        from aleph.core.config import get_settings

        settings = get_settings()
    return str(getattr(settings, name, "") or "").strip()


# --------------------------------------------------------------------------- fechas


_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def parse_datetime(value: Any) -> datetime | None:
    """Fecha de cualquier API (ISO 8601, epoch en segundos o milisegundos, formato de X) en UTC.

    Las fechas sin zona horaria se asumen UTC. Devuelve None si no se puede interpretar.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if abs(value) > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if _NUMERIC_RE.match(text):
            return parse_datetime(float(text))
        try:
            dt = dateutil_parser.isoparse(text)
        except (ValueError, OverflowError):
            try:
                dt = dateutil_parser.parse(text)
            except (ValueError, OverflowError, TypeError):
                return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# --------------------------------------------------------------------------- menciones, hashtags, URLs

_MENTION_RE = re.compile(r"(?<![\w@/.])@([A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,62}[A-Za-z0-9_])?)")
_HASHTAG_RE = re.compile(r"(?<![\w&#/])#(\w{1,100})")
_REDDIT_USER_RE = re.compile(r"(?<![\w/])/?u/([A-Za-z0-9_-]{3,20})(?![\w-])")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def clean_handle(value: Any) -> str:
    """Handle sin @ y sin espacios."""
    return str(value or "").strip().lstrip("@").strip()


def norm_mention(value: Any) -> str:
    return str(value or "").strip().lstrip("@").strip().lower()


def norm_hashtag(value: Any) -> str:
    return str(value or "").strip().lstrip("#").strip().lower()


def extract_mentions(text: str) -> list[str]:
    """Menciones @usuario del texto, en minúscula y sin @."""
    return dedupe(norm_mention(m) for m in _MENTION_RE.findall(text or ""))


def extract_hashtags(text: str) -> list[str]:
    """Hashtags #etiqueta del texto, en minúscula y sin #. Se descartan los sólo numéricos."""
    return dedupe(norm_hashtag(h) for h in _HASHTAG_RE.findall(text or "") if not h.isdigit())


def extract_reddit_mentions(text: str) -> list[str]:
    """Referencias u/usuario de Reddit, sin el prefijo u/."""
    return dedupe(m.lower() for m in _REDDIT_USER_RE.findall(text or ""))


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(".,;:!?")
        if url.endswith(")") and url.count("(") < url.count(")"):
            url = url[:-1]
        if len(url) > 8:
            urls.append(url)
    return dedupe(urls)


def join_text(*parts: Any) -> str:
    return "\n\n".join(str(p).strip() for p in parts if p and str(p).strip())


# --------------------------------------------------------------------------- HTML


_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "ul",
        "ol",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "tr",
        "section",
        "article",
    }
)
_SKIP_TAGS = frozenset({"script", "style"})


class Node:
    """Nodo de un DOM mínimo. Sirve para leer páginas, no para modificarlas."""

    __slots__ = ("attrs", "children", "data", "tag")

    def __init__(self, tag: str, attrs: dict[str, str] | None = None, data: str = "") -> None:
        self.tag = tag  # "#root" para la raíz, "#text" para texto
        self.attrs = attrs or {}
        self.children: list[Node] = []
        self.data = data

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def has_class(self, name: str) -> bool:
        return name in self.classes()

    def walk(self) -> Iterator[Node]:
        for child in self.children:
            yield child
            yield from child.walk()

    def find_all(self, predicate: Callable[[Node], bool]) -> list[Node]:
        return [
            node for node in self.walk() if node.tag not in ("#text", "#root") and predicate(node)
        ]

    def text(self, skip_classes: Iterable[str] = ()) -> str:
        """Texto visible de los hijos. <br> y bloques producen saltos de línea."""
        skip = frozenset(skip_classes)
        parts: list[str] = []

        def visit(node: Node) -> None:
            if node.tag == "#text":
                parts.append(node.data)
                return
            if node.tag in _SKIP_TAGS or (skip and node.classes() & skip):
                return
            if node.tag == "br":
                parts.append("\n")
                return
            block = node.tag in _BLOCK_TAGS
            if block:
                parts.append("\n")
            for child in node.children:
                visit(child)
            if block:
                parts.append("\n")

        for child in self.children:
            visit(child)
        return _tidy("".join(parts))


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self._stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {name: value or "" for name, value in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(Node(tag, {name: value or "" for name, value in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return
        # cierre sin apertura: se ignora

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(Node("#text", data=data))


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html or "")
    builder.close()
    return builder.root


def html_to_text(html: str, skip_classes: Iterable[str] = ()) -> str:
    return parse_html(html or "").text(skip_classes)


def anchor_hrefs(root: Node, exclude_classes: Iterable[str] = ()) -> list[str]:
    """Hrefs absolutos de los enlaces de `root`, salvo los que tengan una de las clases dadas."""
    excluded = frozenset(exclude_classes)
    hrefs: list[str] = []
    for node in root.find_all(lambda n: n.tag == "a"):
        if node.classes() & excluded:
            continue
        href = node.attrs.get("href", "").strip()
        if href.startswith(("http://", "https://")):
            hrefs.append(href)
    return dedupe(hrefs)


# --------------------------------------------------------------------------- exportaciones


_YTD_PREFIX_RE = re.compile(r"^\s*window\.YTD\.[\w.]+\s*=\s*")
_LATIN_HIGH_RE = re.compile(r"[\x80-\xff]+")


def parse_ytd_js(text: str) -> Any:
    """Lee `window.YTD.<nombre>.partN = [...];` (y JSON puro). Ignora lo que sigue al JSON."""
    body = _YTD_PREFIX_RE.sub("", text, count=1).lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(body)
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"archivo exportado ilegible: {exc.msg}") from exc
    return value


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _fix_run(run: str) -> str:
    try:
        return run.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return run


def fix_mojibake(value: str) -> str:
    """Corrige UTF-8 leído como latin-1 (típico de exportaciones de Meta).

    Sólo cambia los tramos que decodifican bien como UTF-8; el resto queda igual.
    """
    if not _LATIN_HIGH_RE.search(value):
        return value
    return _LATIN_HIGH_RE.sub(lambda match: _fix_run(match.group(0)), value)


def fix_mojibake_deep(obj: Any) -> Any:
    if isinstance(obj, str):
        return fix_mojibake(obj)
    if isinstance(obj, list):
        return [fix_mojibake_deep(item) for item in obj]
    if isinstance(obj, dict):
        return {key: fix_mojibake_deep(value) for key, value in obj.items()}
    return obj


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
