"""Búsqueda de nombre de usuario: criterios por estado y por texto, concurrencia y 429. Sin red."""

import asyncio

import httpx
import pytest
from connector_helpers import NO_KEYS, client_for, no_sleep

from aleph.connectors.base import ConnectorError
from aleph.connectors.username_search import UsernameSearchConnector, load_sites

# Primeros ocho sitios del catálogo, en orden: github, gitlab, codeberg, bitbucket, devto, medium,
# reddit y hackernews. El handler responde según el usuario buscado en la ruta.


def _site_handler(log: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(request)
        host, path = request.url.host, request.url.path
        exists = "alguien" in path or "alguien" in str(request.url.params)
        if host == "github.com":
            return httpx.Response(200 if exists else 404)
        if host == "gitlab.com":
            return httpx.Response(404)
        if host == "codeberg.org":
            if path == "/user/login":  # página de inicio de sesión a la que se redirige
                return httpx.Response(200, text="login")
            if exists:  # redirige al login: la URL final ya no tiene el usuario
                return httpx.Response(302, headers={"location": "https://codeberg.org/user/login"})
            return httpx.Response(404)
        if host == "bitbucket.org":
            return httpx.Response(500)
        if host in ("dev.to", "www.reddit.com"):
            return httpx.Response(200 if exists else 404)
        if host == "medium.com":
            return httpx.Response(404)
        if host == "news.ycombinator.com":
            body = "Perfil de usuario" if exists else "No such user."
            return httpx.Response(200, text=body)
        return httpx.Response(404)

    return handler


def test_catalog_is_valid_and_excludes_login_walled_networks():
    sites = load_sites()
    assert len(sites) >= 40
    ids = [s["id"] for s in sites]
    assert len(set(ids)) == len(ids)
    assert all("{username}" in s["url"] for s in sites)
    assert not {"instagram", "x", "facebook", "linkedin", "tiktok"} & set(ids)


async def test_status_and_text_criteria_and_redirect_detection():
    result = await UsernameSearchConnector(
        client=client_for(_site_handler()), settings=NO_KEYS
    ).collect(handle="alguien", limit=8)

    hits = {e.props["site_id"]: e for e in result.entities}
    assert set(hits) == {"github", "devto", "reddit", "hackernews"}
    assert hits["github"].confidence == 0.6  # criterio por estado
    assert hits["hackernews"].confidence == 0.75  # criterio por texto
    assert hits["github"].ref == "account:github:alguien"
    assert hits["github"].label == "alguien"
    assert hits["github"].props["url"] == "https://github.com/alguien"

    outcomes = {o["site"]: o["outcome"] for o in result.raw}
    assert outcomes["gitlab"] == "miss"
    assert outcomes["medium"] == "miss"
    assert outcomes["codeberg"] == "unknown"  # redirigió fuera del perfil
    assert outcomes["bitbucket"] == "unknown"  # error del servidor
    assert any("no concluyentes" in w for w in result.warnings)


async def test_nonexistent_user_yields_no_entities():
    result = await UsernameSearchConnector(
        client=client_for(_site_handler()), settings=NO_KEYS
    ).collect(handle="nadie", limit=8)
    assert result.entities == []


async def test_concurrency_is_bounded():
    state = {"inflight": 0, "peak": 0}

    async def handler(request):
        state["inflight"] += 1
        state["peak"] = max(state["peak"], state["inflight"])
        await asyncio.sleep(0.01)
        state["inflight"] -= 1
        return httpx.Response(404)

    await UsernameSearchConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="alguien", limit=10, concurrency=2
    )
    assert state["peak"] == 2


async def test_429_on_one_site_is_retried_within_cap(monkeypatch):
    waits = no_sleep(monkeypatch)
    hits = {"github": 0}
    base = _site_handler()

    def handler(request):
        if request.url.host == "github.com":
            hits["github"] += 1
            if hits["github"] == 1:
                return httpx.Response(429, headers={"Retry-After": "1"})
        return base(request)

    result = await UsernameSearchConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="alguien", limit=1
    )
    assert waits == [1.0]
    assert [e.props["site_id"] for e in result.entities] == ["github"]


async def test_429_over_cap_marks_only_that_site_as_rate_limited(monkeypatch):
    waits = no_sleep(monkeypatch)
    base = _site_handler()

    def handler(request):
        if request.url.host == "github.com":
            return httpx.Response(429, headers={"Retry-After": "999"})
        return base(request)

    result = await UsernameSearchConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="alguien", limit=2
    )
    assert waits == []
    outcomes = {o["site"]: o["outcome"] for o in result.raw}
    assert outcomes["github"] == "rate_limited"
    assert any("límite de tasa" in w for w in result.warnings)


async def test_invalid_username_is_rejected():
    with pytest.raises(ConnectorError, match="inválido"):
        await UsernameSearchConnector(client=client_for(_site_handler()), settings=NO_KEYS).collect(
            handle="no válido!"
        )
