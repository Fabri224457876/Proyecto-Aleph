"""Ayudantes comunes: fechas en UTC, extracción de menciones/hashtags/URLs, HTML, 429 y mojibake."""

import time
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from connector_helpers import client_for, json_response, no_sleep

from aleph.connectors import _util
from aleph.connectors._util import (
    anchor_hrefs,
    extract_hashtags,
    extract_mentions,
    extract_reddit_mentions,
    extract_urls,
    fix_mojibake,
    get_setting,
    html_to_text,
    parse_datetime,
    parse_html,
    parse_ytd_js,
    positive_int,
    retry_after_seconds,
)
from aleph.connectors.base import ConnectorError

# --------------------------------------------------------------------------- fechas


def test_parse_datetime_returns_utc_for_every_format():
    assert parse_datetime("2024-05-01T10:00:00.000Z") == datetime(2024, 5, 1, 10, tzinfo=UTC)
    assert parse_datetime("2024-01-02T10:00:00+02:00") == datetime(2024, 1, 2, 8, tzinfo=UTC)
    assert parse_datetime(1714557600) == datetime(2024, 5, 1, 10, tzinfo=UTC)
    assert parse_datetime(1714557600123).replace(microsecond=0) == datetime(
        2024, 5, 1, 10, tzinfo=UTC
    )
    assert parse_datetime("Wed Oct 10 20:19:24 +0000 2018") == datetime(
        2018, 10, 10, 20, 19, 24, tzinfo=UTC
    )
    # Sin zona horaria se asume UTC.
    assert parse_datetime("2024-03-01T00:00:00") == datetime(2024, 3, 1, tzinfo=UTC)


def test_parse_datetime_rejects_garbage():
    assert parse_datetime("no es fecha") is None
    assert parse_datetime(None) is None
    assert parse_datetime("") is None
    assert parse_datetime(True) is None


# --------------------------------------------------------------------------- menciones, hashtags, URLs


def test_mentions_hashtags_and_urls_are_normalized_from_text():
    text = "Hola @Ana.Bsky.social. Mail a@b.com y #CTI #cti #123 #Ñandú, ver https://ejemplo.example/x)."
    assert extract_mentions(text) == ["ana.bsky.social"]
    assert extract_hashtags(text) == ["cti", "ñandú"]
    assert extract_urls(text) == ["https://ejemplo.example/x"]


def test_reddit_mentions_drop_the_u_prefix():
    assert extract_reddit_mentions("Ver u/Otro_User y /u/otro_user, no r/sub") == ["otro_user"]


# --------------------------------------------------------------------------- HTML


def test_html_to_text_keeps_breaks_and_skips_hidden_parts():
    html = (
        '<p>Hola <a href="https://m.example/@bob" class="mention">@<span>bob</span></a></p>'
        '<p>Fin<br>línea</p><span class="invisible">https://</span>'
    )
    assert html_to_text(html, skip_classes=("invisible",)) == "Hola @bob\n\nFin\nlínea"


def test_anchor_hrefs_excludes_mention_and_hashtag_links():
    root = parse_html(
        '<a href="https://m.example/@bob" class="u-url mention">@bob</a> '
        '<a class="mention hashtag" href="https://m.example/tags/cti">#cti</a> '
        '<a href="https://ejemplo.example/x">x</a> <a href="/relativo">rel</a>'
    )
    assert anchor_hrefs(root, exclude_classes=("mention", "hashtag")) == [
        "https://ejemplo.example/x"
    ]


# --------------------------------------------------------------------------- mojibake y archivos JS


def test_fix_mojibake_repairs_utf8_read_as_latin1_and_keeps_real_text():
    assert fix_mojibake("CafÃ©") == "Café"
    assert fix_mojibake("ð\u009f\u0098\u0080 ok") == "😀 ok"
    assert fix_mojibake("Niño y árbol") == "Niño y árbol"


def test_parse_ytd_js_ignores_the_assignment_and_the_semicolon():
    assert parse_ytd_js('window.YTD.tweets.part0 = [{"a": 1}];') == [{"a": 1}]
    assert parse_ytd_js("[1, 2]") == [1, 2]


# --------------------------------------------------------------------------- espera ante 429


def test_retry_after_parsing_follows_each_api_convention():
    assert retry_after_seconds(httpx.Response(429, headers={"Retry-After": "7"})) == 7.0
    assert retry_after_seconds(httpx.Response(429)) == _util.DEFAULT_BACKOFF
    reset = int(time.time()) + 100
    waited = retry_after_seconds(httpx.Response(429, headers={"x-ratelimit-reset": str(reset)}))
    assert 95 <= waited <= 100
    assert retry_after_seconds(httpx.Response(429, headers={"X-RateLimit-Reset": "30"})) == 30.0


async def test_429_within_cap_waits_retry_after_and_retries(monkeypatch):
    waits = no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return json_response({"ok": True})

    async with _util.HttpSession(client_for(handler)) as http:
        assert await http.get_json("https://api.example/x") == {"ok": True}
    assert waits == [2.0]
    assert calls["n"] == 2


async def test_429_over_cap_raises_rate_limited_without_waiting(monkeypatch):
    waits = no_sleep(monkeypatch)

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "3600"})

    async with _util.HttpSession(client_for(handler)) as http:
        with pytest.raises(_util.RateLimited) as info:
            await http.get_json("https://api.example/x")
    assert info.value.wait == 3600
    assert waits == []


async def test_429_repeated_beyond_max_retries_raises(monkeypatch):
    waits = no_sleep(monkeypatch)

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "1"})

    async with _util.HttpSession(client_for(handler), max_retries=2) as http:
        with pytest.raises(_util.RateLimited):
            await http.get_json("https://api.example/x")
    assert waits == [1.0, 1.0]


# --------------------------------------------------------------------------- errores HTTP


async def test_timeout_and_network_errors_become_connector_errors():
    def handler(request):
        raise httpx.ReadTimeout("tardó demasiado", request=request)

    async with _util.HttpSession(client_for(handler)) as http:
        with pytest.raises(ConnectorError, match="tiempo de espera"):
            await http.get_json("https://api.example/x")


async def test_missing_resource_returns_none_and_server_error_raises():
    def handler(request):
        return httpx.Response(404) if "missing" in request.url.path else httpx.Response(500)

    async with _util.HttpSession(client_for(handler)) as http:
        assert await http.get_json("https://api.example/missing") is None
        with pytest.raises(ConnectorError, match="HTTP 500"):
            await http.get_json("https://api.example/boom")


async def test_auth_errors_never_echo_the_credential():
    def handler(request):
        return httpx.Response(401)

    headers = {"Authorization": "Bearer SECRETO-123"}
    async with _util.HttpSession(client_for(handler), headers=headers) as http:
        with pytest.raises(ConnectorError) as info:
            await http.get_json("https://api.example/x")
    assert "SECRETO-123" not in str(info.value)


async def test_session_creates_and_closes_its_own_client_when_none_is_injected():
    async with _util.HttpSession(None) as http:
        assert isinstance(http._client, httpx.AsyncClient)


# --------------------------------------------------------------------------- parámetros y ajustes


def test_positive_int_validates_and_caps():
    assert positive_int(None, "limit", 50) == 50
    assert positive_int("7", "limit", 50) == 7
    assert positive_int(500, "limit", 50, maximum=100) == 100
    with pytest.raises(ConnectorError):
        positive_int(0, "limit", 50)
    with pytest.raises(ConnectorError):
        positive_int("abc", "limit", 50)


def test_get_setting_reads_and_strips_values():
    assert get_setting(SimpleNamespace(github_token="  tok  "), "github_token") == "tok"
    assert get_setting(SimpleNamespace(), "github_token") == ""
