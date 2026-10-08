"""Favicon de un sitio: hash MurmurHash3 de 32 bits del base64 del icono, al estilo de Shodan,
y la consulta de Shodan correspondiente (se construye, no se ejecuta).

Referencias:
- Shodan, «Deep Dive: http.favicon»: https://blog.shodan.io/deep-dive-http-favicon/
  (el hash es MurmurHash3 sobre el base64 del icono, con saltos de línea, y se usa como entero con signo).
- MurmurHash3 y sus vectores de verificación (SMHasher): https://en.wikipedia.org/wiki/MurmurHash

Procedimiento: se pide /favicon.ico; si no hay icono válido, se lee la portada y se buscan
<link rel="icon">. Cada URL pasa por ensure_public_http_url y cada redirección se revalida, así que la
herramienta no alcanza redes privadas aunque el sitio redirija hacia ellas.
"""

from __future__ import annotations

import base64
import hashlib
from contextlib import suppress
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

import httpx
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from ._http import FetchedResource, ensure_public_http_url, fetch_public, open_client
from .errors import InvalidInputError, OsintError

MAX_ICON_BYTES = 512 * 1024
MAX_HTML_BYTES = 512 * 1024
MAX_ICON_CANDIDATES = 3
_MASK32 = 0xFFFFFFFF
_C1 = 0xCC9E2D51
_C2 = 0x1B873593


def _rotl32(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (32 - shift))) & _MASK32


def murmur3_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3_x86_32 en Python puro. Devuelve el valor sin signo."""
    h = seed & _MASK32
    length = len(data)
    blocks = length // 4
    for index in range(blocks):
        k = int.from_bytes(data[4 * index:4 * index + 4], "little")
        k = (k * _C1) & _MASK32
        k = _rotl32(k, 15)
        k = (k * _C2) & _MASK32
        h ^= k
        h = _rotl32(h, 13)
        h = (h * 5 + 0xE6546B64) & _MASK32
    tail = data[blocks * 4:]
    k = 0
    if len(tail) >= 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * _C1) & _MASK32
        k = _rotl32(k, 15)
        k = (k * _C2) & _MASK32
        h ^= k
    h ^= length
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & _MASK32
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & _MASK32
    h ^= h >> 16
    return h


def to_signed32(value: int) -> int:
    return value - (1 << 32) if value >= (1 << 31) else value


def favicon_hash(content: bytes) -> int:
    """Hash estilo Shodan: MurmurHash3 de 32 bits sobre base64 MIME (líneas de 76 y salto final), con signo."""
    if not content:
        raise InvalidInputError("el icono está vacío")
    return to_signed32(murmur3_32(base64.encodebytes(content)))


def shodan_query(hash_value: int) -> str:
    return f"http.favicon.hash:{hash_value}"


def shodan_search_url(hash_value: int) -> str:
    return f"https://www.shodan.io/search?query={quote(shodan_query(hash_value), safe='')}"


class FaviconResult(BaseModel):
    site: str
    found: bool
    favicon_url: str = ""
    source: str = ""  # «favicon.ico» o «link rel=icon» de la portada
    content_type: str = ""
    size_bytes: int = 0
    sha256: str = ""
    mmh3_hash: int | None = None  # con signo, como lo indexa Shodan
    shodan_query: str = ""
    shodan_search_url: str = ""
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, object] = Field(default_factory=dict)


class _IconLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        values = {name.lower(): (value or "") for name, value in attrs}
        if "icon" in values.get("rel", "").lower().split() and values.get("href", "").strip():
            self.hrefs.append(values["href"].strip())


def icon_links(html: bytes, base_url: str) -> list[str]:
    parser = _IconLinkParser()
    with suppress(Exception):  # HTML malformado: se usan los enlaces hallados hasta el error
        parser.feed(html.decode("utf-8", errors="replace"))
    links: list[str] = []
    for href in parser.hrefs:
        absolute = urljoin(base_url, href)
        try:
            links.append(ensure_public_http_url(absolute))
        except OsintError:
            continue
        if len(links) >= MAX_ICON_CANDIDATES:
            break
    return links


def _looks_like_icon(resource: FetchedResource) -> bool:
    """Un icono válido es 200, no vacío, no truncado (un hash parcial sería falso) y no es texto."""
    if resource.status_code != 200 or not resource.content or resource.truncated:
        return False
    media = resource.content_type.split(";")[0].strip().lower()
    return not (media.startswith("text/") or media == "application/json")


async def analyze_favicon(
    site: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> FaviconResult:
    """Descarga el favicon de un sitio y calcula su hash estilo Shodan. Solo lectura."""
    value = (site or "").strip()
    if not value:
        raise InvalidInputError("sitio vacío")
    if "://" not in value:
        value = f"https://{value}"
    page_url = ensure_public_http_url(value)
    parts = urlsplit(page_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    warnings: list[str] = []

    async with open_client(client) as http:
        icon = None
        source = ""
        try:
            direct = await fetch_public(http, urljoin(origin, "/favicon.ico"), max_bytes=MAX_ICON_BYTES)
            if _looks_like_icon(direct):
                icon, source = direct, "favicon.ico"
            elif direct.truncated:
                warnings.append("favicon.ico supera el tamaño máximo; no se calcula su hash")
        except OsintError as exc:
            warnings.append(f"favicon.ico no disponible: {exc}")
        if icon is None:
            try:
                home = await fetch_public(http, page_url, max_bytes=MAX_HTML_BYTES)
            except OsintError as exc:
                warnings.append(f"portada no disponible: {exc}")
            else:
                for link in icon_links(home.content, home.url):
                    try:
                        candidate = await fetch_public(http, link, max_bytes=MAX_ICON_BYTES)
                    except OsintError as exc:
                        warnings.append(f"icono enlazado no disponible: {exc}")
                        continue
                    if _looks_like_icon(candidate):
                        icon, source = candidate, "link rel=icon"
                        break
                    if candidate.truncated:
                        warnings.append(f"icono enlazado {candidate.url} supera el tamaño máximo")

    host = parts.hostname or ""
    if icon is None:
        warnings.append("no se encontró un icono válido")
        return FaviconResult(site=page_url, found=False, warnings=warnings)

    digest = favicon_hash(icon.content)
    query = shodan_query(digest)
    sha = hashlib.sha256(icon.content).hexdigest()
    entities = [
        EntityRecord(type="domain", label=host, ref="site", confidence=1.0, props={"origen": origin}),
        EntityRecord(type="hash", label=f"favicon mmh3 {digest}", ref="favicon", confidence=1.0,
                     props={"algorithm": "mmh3-32 sobre base64 MIME (estilo Shodan)", "value": digest,
                            "sha256": sha, "favicon_url": icon.url}),
    ]
    relations = [RelationRecord(src_ref="site", dst_ref="favicon", type="has_favicon_hash", confidence=1.0)]
    return FaviconResult(
        site=page_url, found=True, favicon_url=icon.url, source=source,
        content_type=icon.content_type.split(";")[0].strip(), size_bytes=len(icon.content),
        sha256=sha, mmh3_hash=digest, shodan_query=query, shodan_search_url=shodan_search_url(digest),
        entities=entities, relations=relations, warnings=warnings,
        raw={"url": icon.url, "status_code": icon.status_code, "content_type": icon.content_type,
             "content_base64": base64.b64encode(icon.content).decode("ascii")},
    )
