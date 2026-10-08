import base64
import hashlib

import httpx
import pytest

from aleph.osint.errors import InvalidInputError, UnsafeTargetError
from aleph.osint.favicon import (
    analyze_favicon,
    favicon_hash,
    icon_links,
    murmur3_32,
    shodan_query,
    to_signed32,
)

from .helpers import mock_client, text_response

ICON = bytes(range(256)) * 3  # 768 bytes de contenido binario sintético


def test_murmur3_known_vectors():
    assert murmur3_32(b"") == 0
    assert murmur3_32(b"", 1) == 0x514E28B7
    assert murmur3_32(b"hello") == 0x248BFA47
    assert murmur3_32(b"abc") == 0xB3DD93FA
    assert murmur3_32(b"foo") == 0xF6A5C420


def test_murmur3_smhasher_verification_value():
    # SMHasher: hash de cada prefijo de la clave 0..255 con semilla 256-i, y luego el conjunto con semilla 0.
    key = bytes(range(256))
    digests = b"".join(murmur3_32(key[:i], 256 - i).to_bytes(4, "little") for i in range(256))
    assert murmur3_32(digests, 0) == 0xB0F57EE3


def test_signed_conversion():
    assert to_signed32(0xF6A5C420) == -156908512
    assert to_signed32(0x7FFFFFFF) == 2147483647
    assert to_signed32(0x80000000) == -2147483648


def test_favicon_hash_uses_mime_base64_with_76_char_lines_and_trailing_newline():
    content = bytes(range(200))
    encoded = base64.b64encode(content).decode("ascii")
    lines = [encoded[i:i + 76] for i in range(0, len(encoded), 76)]
    mime_text = ("\n".join(lines) + "\n").encode("ascii")
    assert favicon_hash(content) == to_signed32(murmur3_32(mime_text))
    # Sin los saltos de línea el hash sería otro: el formato importa
    assert favicon_hash(content) != to_signed32(murmur3_32(encoded.encode("ascii")))


def test_shodan_query_format():
    assert shodan_query(-1040449965) == "http.favicon.hash:-1040449965"


def test_empty_icon_has_no_hash():
    with pytest.raises(InvalidInputError):
        favicon_hash(b"")


def test_icon_links_are_extracted_and_resolved():
    html = (b'<html><head><link rel="stylesheet" href="/s.css">'
            b'<link rel="shortcut icon" href="/static/ico.png">'
            b'<link rel="apple-touch-icon" href="/apple.png">'
            b'<link rel="icon" href="https://cdn.example.com/i.svg"></head></html>')
    links = icon_links(html, "https://example.com/inicio/")
    assert links == ["https://example.com/static/ico.png", "https://cdn.example.com/i.svg"]


async def test_fallback_to_link_rel_icon_when_favicon_ico_missing():
    home = (b'<html><head><link rel="shortcut icon" href="/static/icon-32.png"></head></html>')
    requested = []

    def handler(request: httpx.Request):
        requested.append(str(request.url))
        if request.url.path == "/favicon.ico":
            return text_response("not found", status=404)
        if request.url.path == "/":
            return httpx.Response(200, content=home, headers={"content-type": "text/html; charset=utf-8"})
        if request.url.path == "/static/icon-32.png":
            return httpx.Response(200, content=ICON, headers={"content-type": "image/png"})
        return text_response("nope", status=404)

    async with mock_client(handler) as client:
        result = await analyze_favicon("example.com", client=client)

    assert result.found is True
    assert result.source == "link rel=icon"
    assert result.favicon_url == "https://example.com/static/icon-32.png"
    assert result.content_type == "image/png"
    assert result.size_bytes == len(ICON)
    assert result.sha256 == hashlib.sha256(ICON).hexdigest()
    assert result.mmh3_hash == favicon_hash(ICON)
    assert result.shodan_query == f"http.favicon.hash:{favicon_hash(ICON)}"
    assert "shodan.io/search?query=" in result.shodan_search_url
    assert base64.b64decode(result.raw["content_base64"]) == ICON
    assert requested[0] == "https://example.com/favicon.ico"
    assert {e.type for e in result.entities} == {"domain", "hash"}
    assert result.relations[0].type == "has_favicon_hash"


async def test_html_soft_404_for_favicon_ico_is_not_an_icon():
    def handler(request: httpx.Request):
        if request.url.path == "/favicon.ico":
            return httpx.Response(200, content=b"<html>not an icon</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    async with mock_client(handler) as client:
        result = await analyze_favicon("https://example.com", client=client)
    assert result.found is False
    assert any("no se encontró" in w for w in result.warnings)


async def test_direct_favicon_ico_is_used_first():
    def handler(request: httpx.Request):
        if request.url.path == "/favicon.ico":
            return httpx.Response(200, content=ICON, headers={"content-type": "image/x-icon"})
        raise AssertionError("no debía pedirse la portada")

    async with mock_client(handler) as client:
        result = await analyze_favicon("example.com", client=client)
    assert result.source == "favicon.ico"
    assert result.mmh3_hash == favicon_hash(ICON)


async def test_oversized_icon_is_not_hashed(monkeypatch):
    from aleph.osint import favicon

    monkeypatch.setattr(favicon, "MAX_ICON_BYTES", 100)

    def handler(request: httpx.Request):
        if request.url.path == "/favicon.ico":
            return httpx.Response(200, content=ICON, headers={"content-type": "image/png"})
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    async with mock_client(handler) as client:
        result = await analyze_favicon("example.com", client=client)
    assert result.found is False
    assert any("supera el tamaño" in w for w in result.warnings)


@pytest.mark.parametrize("site", [
    "http://127.0.0.1/", "http://localhost:8080", "https://169.254.169.254/latest",
    "http://[::1]/", "http://10.0.0.5", "http://100.64.0.10:4000", "http://2130706433/",
    "http://files.internal/", "http://user:pass@example.com/",
])
async def test_private_targets_are_refused_before_any_request(site):
    def handler(request: httpx.Request):  # pragma: no cover - no debe ejecutarse
        raise AssertionError(f"se intentó consultar {request.url}")

    async with mock_client(handler) as client:
        with pytest.raises((UnsafeTargetError, InvalidInputError)):
            await analyze_favicon(site, client=client)


async def test_redirect_to_private_address_is_not_followed():
    requested = []

    def handler(request: httpx.Request):
        requested.append(str(request.url))
        if request.url.path == "/favicon.ico":
            return httpx.Response(302, headers={"location": "http://10.1.2.3/secreto.ico"})
        return httpx.Response(302, headers={"location": "http://192.168.0.1/"})

    async with mock_client(handler) as client:
        result = await analyze_favicon("example.com", client=client)
    assert result.found is False
    assert not any("10.1.2.3" in url or "192.168.0.1" in url for url in requested)
    assert result.warnings


async def test_empty_site_rejected():
    async with mock_client(lambda r: httpx.Response(500)) as client:
        with pytest.raises(InvalidInputError):
            await analyze_favicon("   ", client=client)
