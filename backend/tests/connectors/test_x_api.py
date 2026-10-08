"""X API v2 oficial: usuario, línea de tiempo con pagination_token y tipos de referencia. Sin red.

Forma de la respuesta según la documentación de X consultada (users/get-posts): data[], meta.next_token,
entities (hashtags[].tag, mentions[].username, urls[].expanded_url), referenced_tweets[].type.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from connector_helpers import NO_KEYS, client_for, json_response, no_sleep

from aleph.connectors.base import ConnectorError
from aleph.connectors.x_api import XApiConnector

TOKEN = "tok-abc-123"
SETTINGS = SimpleNamespace(x_bearer_token=TOKEN, github_token="")

USER = {
    "id": "1001",
    "name": "Ejemplo",
    "username": "ejemplo",
    "description": "Bio de prueba",
    "created_at": "2010-01-01T00:00:00.000Z",
    "verified": False,
    "protected": False,
    "location": "Ciudad Demo",
    "profile_image_url": "https://pbs.example/p.jpg",
    "public_metrics": {"followers_count": 50, "following_count": 10, "tweet_count": 300,
                       "listed_count": 2},
    "entities": {"url": {"urls": [{"url": "https://t.co/zz",
                                   "expanded_url": "https://ejemplo.example",
                                   "display_url": "ejemplo.example"}]}},
}
T1 = {
    "id": "5001",
    "text": "Hola #OSINT con @Fuente y https://ejemplo.example/nota",
    "created_at": "2024-02-01T10:00:00.000Z",
    "author_id": "1001",
    "lang": "es",
    "source": "Twitter Web App",
    "conversation_id": "5001",
    "entities": {
        "hashtags": [{"start": 5, "end": 11, "tag": "OSINT"}],
        "mentions": [{"start": 16, "end": 23, "username": "Fuente", "id": "77"}],
        "urls": [{"start": 28, "end": 51, "url": "https://t.co/x1",
                  "expanded_url": "https://ejemplo.example/nota",
                  "display_url": "ejemplo.example/nota"}],
    },
    "public_metrics": {"retweet_count": 1, "reply_count": 0, "like_count": 4, "quote_count": 0},
}
T2 = {  # respuesta sin entities: menciones y hashtags se extraen del texto
    "id": "5002",
    "text": "@ejemplo respuesta corta #Demo",
    "created_at": "2024-02-02T10:00:00.000Z",
    "author_id": "1001",
    "lang": "und",
    "source": "Twitter for Android",
    "referenced_tweets": [{"type": "replied_to", "id": "5000"}],
    "in_reply_to_user_id": "1002",
}
T3 = {  # cita
    "id": "5003",
    "text": "Mirá esto https://x.com/otro/status/4000",
    "created_at": "2024-02-03T10:00:00.000Z",
    "referenced_tweets": [{"type": "quoted", "id": "4000"}],
    "lang": "es",
    "entities": {"urls": [{"url": "https://t.co/q",
                           "expanded_url": "https://x.com/otro/status/4000"}]},
}
T4 = {  # retweet
    "id": "5004",
    "text": "RT @fuente: contenido",
    "created_at": "2024-02-04T10:00:00.000Z",
    "referenced_tweets": [{"type": "retweeted", "id": "3000"}],
    "entities": {"mentions": [{"start": 3, "end": 10, "username": "fuente", "id": "77"}]},
}


def _handler(log: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        path = request.url.path
        if path == "/2/users/by/username/ejemplo":
            return json_response({"data": USER})
        if path == "/2/users/by/username/nadie":
            return json_response({"errors": [{"title": "Not Found Error"}]}, status=404)
        if path == "/2/users/1001/tweets":
            if request.url.params.get("pagination_token") == "TOKEN2":
                return json_response({"data": [T3, T4], "meta": {"result_count": 2}})
            return json_response({"data": [T1, T2],
                                  "meta": {"result_count": 2, "next_token": "TOKEN2"}})
        if path == "/2/users/1001/following":
            return json_response({"data": [{"id": "9", "username": "Amigo"},
                                           {"id": "10", "username": "otra"}],
                                  "meta": {"result_count": 2}})
        return json_response({"errors": [{"title": "Unsupported"}]}, status=400)

    return handler


async def test_collects_user_timeline_pages_and_following():
    log: list[httpx.Request] = []
    result = await XApiConnector(client=client_for(_handler(log)), settings=SETTINGS).collect(
        handle="@ejemplo", limit=4, graph_limit=5
    )
    (profile,) = result.profiles
    acc = profile.account
    assert acc.platform == "x" and acc.handle == "ejemplo"
    assert acc.platform_uid == "1001"
    assert acc.followers == 50 and acc.following == 10
    assert acc.following_handles == ["Amigo", "otra"]
    assert acc.created_at_platform == datetime(2010, 1, 1, tzinfo=UTC)

    posts = profile.posts
    assert [p.kind for p in posts] == ["original", "reply", "quote", "repost"]

    first = posts[0]
    assert first.platform_post_id == "5001"
    assert first.hashtags == ["osint"]
    assert first.mentions == ["fuente"]
    assert first.urls == ["https://ejemplo.example/nota"]  # expanded_url
    assert first.lang == "es"
    assert first.client == "Twitter Web App"
    assert first.created_at == datetime(2024, 2, 1, 10, tzinfo=UTC)

    reply = posts[1]
    assert reply.reply_to == "5000"
    assert reply.mentions == ["ejemplo"]  # sin entities: del texto
    assert reply.hashtags == ["demo"]
    assert reply.lang == ""  # "und" se descarta
    assert reply.client == "Twitter for Android"

    assert posts[2].meta["quoted_id"] == "4000"
    assert posts[2].urls == ["https://x.com/otro/status/4000"]
    assert posts[3].meta["retweeted_id"] == "3000"
    assert posts[3].mentions == ["fuente"]

    # Paginación: dos llamadas a la línea de tiempo, la segunda con el token de la primera.
    timeline = [r for r in log if r.url.path.endswith("/tweets")]
    assert len(timeline) == 2
    assert timeline[1].url.params["pagination_token"] == "TOKEN2"

    # El token viaja sólo como cabecera, nunca en la URL.
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in log)
    assert TOKEN not in str(log[0].url)
    assert TOKEN not in result.model_dump_json()

    website = [e for e in result.entities if e.type == "url"]
    assert website and website[0].label == "https://ejemplo.example"


async def test_missing_token_fails_before_any_request():
    def handler(request):
        raise AssertionError("sin token no debe haber ninguna consulta")

    with pytest.raises(ConnectorError, match="ALEPH_X_BEARER_TOKEN"):
        await XApiConnector(client=client_for(handler), settings=NO_KEYS).collect(handle="ejemplo")


async def test_unknown_user_raises():
    with pytest.raises(ConnectorError, match="no encontrado"):
        await XApiConnector(client=client_for(_handler([])), settings=SETTINGS).collect(
            handle="nadie"
        )


async def test_429_within_cap_is_retried(monkeypatch):
    waits = no_sleep(monkeypatch)
    hits = {"timeline": 0}

    def handler(request):
        if request.url.path == "/2/users/by/username/ejemplo":
            return json_response({"data": USER})
        if request.url.path.endswith("/following"):
            return json_response({"data": []})
        hits["timeline"] += 1
        if hits["timeline"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return json_response({"data": [T1], "meta": {"result_count": 1}})

    result = await XApiConnector(client=client_for(handler), settings=SETTINGS).collect(
        handle="ejemplo", limit=1
    )
    assert waits == [1.0]
    assert [p.platform_post_id for p in result.profiles[0].posts] == ["5001"]


async def test_429_over_cap_returns_profile_and_warning(monkeypatch):
    waits = no_sleep(monkeypatch)

    def handler(request):
        if request.url.path == "/2/users/by/username/ejemplo":
            return json_response({"data": USER})
        if request.url.path.endswith("/following"):
            return json_response({"data": []})
        return httpx.Response(429, headers={"Retry-After": "900"})

    result = await XApiConnector(client=client_for(handler), settings=SETTINGS).collect(
        handle="ejemplo", limit=5
    )
    assert waits == []
    assert result.profiles[0].posts == []
    assert result.profiles[0].account.followers == 50
    assert any("Límite de tasa" in w for w in result.warnings)
