"""Bluesky: perfil, feed con paginación por cursor, grafo y tipos de publicación. Sin red.

Formas de respuesta según los lexicones oficiales de atproto (feed/defs, actor/defs, graph/getFollows).
"""

from datetime import UTC, datetime

import httpx
import pytest
from connector_helpers import NO_KEYS, client_for, json_response, no_sleep

from aleph.connectors.base import ConnectorError
from aleph.connectors.bluesky import BlueskyConnector

PROFILE = {
    "did": "did:plc:ejemplo123",
    "handle": "ejemplo.bsky.social",
    "displayName": "Cuenta Demo",
    "description": "Cuenta de ejemplo",
    "avatar": "https://cdn.example/avatar.jpg",
    "followersCount": 1500,
    "followsCount": 20,
    "postsCount": 4,
    "createdAt": "2023-01-01T00:00:00.000Z",
    "indexedAt": "2024-01-01T00:00:00.000Z",
    "website": "https://sitio.example",
}
AUTHOR = {"did": "did:plc:ejemplo123", "handle": "ejemplo.bsky.social"}
OTHER = {"did": "did:plc:otro", "handle": "otro.bsky.social"}
PARENT_URI = "at://did:plc:otro/app.bsky.feed.post/3kzzz"

FEED_PAGE_1 = {
    "feed": [
        {  # original: hashtag, mención y enlace vienen de facets y del texto
            "post": {
                "uri": "at://did:plc:ejemplo123/app.bsky.feed.post/3kaaa",
                "cid": "bafy1",
                "author": AUTHOR,
                "record": {
                    "$type": "app.bsky.feed.post",
                    "text": "Nota sobre #OSINT con @ana.bsky.social y un enlace",
                    "createdAt": "2024-05-01T10:00:00.000Z",
                    "langs": ["es"],
                    "facets": [
                        {"index": {"byteStart": 10, "byteEnd": 16},
                         "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": "OSINT"}]},
                        {"index": {"byteStart": 22, "byteEnd": 40},
                         "features": [{"$type": "app.bsky.richtext.facet#mention",
                                       "did": "did:plc:ana"}]},
                        {"index": {"byteStart": 50, "byteEnd": 60},
                         "features": [{"$type": "app.bsky.richtext.facet#link",
                                       "uri": "https://ejemplo.example/nota"}]},
                    ],
                },
                "replyCount": 0,
                "repostCount": 2,
                "likeCount": 5,
                "indexedAt": "2024-05-01T10:00:05.000Z",
            },
        },
        {  # respuesta: el padre está en record.reply.parent
            "post": {
                "uri": "at://did:plc:ejemplo123/app.bsky.feed.post/3kbbb",
                "cid": "bafy2",
                "author": AUTHOR,
                "record": {
                    "$type": "app.bsky.feed.post",
                    "text": "Respondo a @otro.bsky.social",
                    "createdAt": "2024-05-02T09:00:00.000Z",
                    "langs": ["es"],
                    "reply": {"root": {"uri": PARENT_URI, "cid": "bafyz"},
                              "parent": {"uri": PARENT_URI, "cid": "bafyz"}},
                },
                "indexedAt": "2024-05-02T09:00:04.000Z",
            },
            "reply": {"root": {"uri": PARENT_URI}, "parent": {"uri": PARENT_URI}},
        },
        {  # repost del propio usuario: el post es de otro autor
            "post": {
                "uri": PARENT_URI,
                "cid": "bafyz",
                "author": OTHER,
                "record": {"$type": "app.bsky.feed.post", "text": "Post ajeno #Cti",
                           "createdAt": "2024-04-20T08:00:00.000Z", "langs": []},
                "indexedAt": "2024-04-20T08:00:03.000Z",
            },
            "reason": {
                "$type": "app.bsky.feed.defs#reasonRepost",
                "by": AUTHOR,
                "indexedAt": "2024-05-03T07:30:00.000Z",
            },
        },
        {  # fijado: ya aparece en su posición normal, así que se omite
            "post": {"uri": "at://did:plc:ejemplo123/app.bsky.feed.post/3kaaa", "cid": "bafy1",
                     "author": AUTHOR, "record": {"text": "Nota sobre #OSINT"},
                     "indexedAt": "2024-05-01T10:00:05.000Z"},
            "reason": {"$type": "app.bsky.feed.defs#reasonPin"},
        },
    ],
    "cursor": "cursor-2",
}

FEED_PAGE_2 = {
    "feed": [
        {  # cita: el post citado aparece en la vista del embed
            "post": {
                "uri": "at://did:plc:ejemplo123/app.bsky.feed.post/3kddd",
                "cid": "bafy4",
                "author": AUTHOR,
                "record": {
                    "$type": "app.bsky.feed.post",
                    "text": "Mirá esto",
                    "createdAt": "2024-05-04T12:00:00.000Z",
                    "langs": ["en", "es"],
                    "embed": {"$type": "app.bsky.embed.record",
                              "record": {"uri": PARENT_URI, "cid": "bafyz"}},
                },
                "embed": {
                    "$type": "app.bsky.embed.record#view",
                    "record": {
                        "$type": "app.bsky.embed.record#viewRecord",
                        "uri": PARENT_URI,
                        "cid": "bafyz",
                        "author": OTHER,
                        "value": {},
                    },
                },
                "indexedAt": "2024-05-04T12:00:02.000Z",
            },
        },
    ],
}


def _handler(log: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        path = request.url.path
        if path.endswith("getProfile"):
            return json_response(PROFILE)
        if path.endswith("getAuthorFeed"):
            if request.url.params.get("cursor") == "cursor-2":
                return json_response(FEED_PAGE_2)
            return json_response(FEED_PAGE_1)
        if path.endswith("getFollows"):
            return json_response({"follows": [{"handle": "ana.bsky.social"},
                                              {"handle": "otro.bsky.social"}]})
        if path.endswith("getFollowers"):
            return json_response({"followers": [{"handle": "fan.bsky.social"}]})
        return httpx.Response(404)

    return handler


async def test_collects_profile_feed_pages_and_graph():
    log: list[httpx.Request] = []
    result = await BlueskyConnector(client=client_for(_handler(log)), settings=NO_KEYS).collect(
        handle="ejemplo.bsky.social", limit=10, graph_limit=5
    )
    (profile,) = result.profiles
    acc = profile.account
    assert acc.platform == "bluesky" and acc.handle == "ejemplo.bsky.social"
    assert acc.platform_uid == "did:plc:ejemplo123"
    assert acc.followers == 1500 and acc.following == 20
    assert acc.created_at_platform == datetime(2023, 1, 1, tzinfo=UTC)
    assert acc.following_handles == ["ana.bsky.social", "otro.bsky.social"]
    assert acc.follower_handles == ["fan.bsky.social"]
    assert acc.meta["website"] == "https://sitio.example"

    posts = profile.posts
    assert [p.kind for p in posts] == ["original", "reply", "repost", "quote"]

    original = posts[0]
    assert original.hashtags == ["osint"]
    assert original.mentions == ["ana.bsky.social"]
    assert original.urls == ["https://ejemplo.example/nota"]
    assert original.lang == "es"
    assert original.created_at == datetime(2024, 5, 1, 10, tzinfo=UTC)
    assert original.created_at.utcoffset().total_seconds() == 0

    reply = posts[1]
    assert reply.reply_to == PARENT_URI
    assert reply.mentions == ["otro.bsky.social"]

    repost = posts[2]
    assert repost.platform_post_id == f"repost:{PARENT_URI}"
    assert repost.created_at == datetime(2024, 5, 3, 7, 30, tzinfo=UTC)  # cuándo se repostó
    assert repost.hashtags == ["cti"]  # sin facets: se extrae del texto
    assert repost.reply_to == ""
    assert repost.meta["repost_by"] == "ejemplo.bsky.social"
    assert repost.meta["original_author"] == "otro.bsky.social"

    quote = posts[3]
    assert quote.meta["quoted_uri"] == PARENT_URI
    assert quote.meta["quoted_handle"] == "otro.bsky.social"
    assert quote.lang == "en"

    # Paginación: dos páginas del feed; la segunda usa el cursor de la primera.
    feed_calls = [r for r in log if r.url.path.endswith("getAuthorFeed")]
    assert len(feed_calls) == 2
    assert feed_calls[0].url.params["limit"] == "10"
    assert feed_calls[1].url.params["cursor"] == "cursor-2"
    assert result.raw["profile"]["handle"] == "ejemplo.bsky.social"


async def test_missing_account_raises_a_clear_error():
    def handler(request):
        return json_response({"error": "InvalidRequest", "message": "Profile not found"},
                             status=400)

    with pytest.raises(ConnectorError, match="no encontrada"):
        await BlueskyConnector(client=client_for(handler), settings=NO_KEYS).collect(
            handle="nadie.bsky.social"
        )


async def test_429_within_cap_is_retried(monkeypatch):
    waits = no_sleep(monkeypatch)
    hits = {"feed": 0}

    def handler(request):
        if request.url.path.endswith("getProfile"):
            return json_response(PROFILE)
        if request.url.path.endswith("getAuthorFeed"):
            hits["feed"] += 1
            if hits["feed"] == 1:
                return httpx.Response(429, headers={"Retry-After": "2"})
            return json_response(FEED_PAGE_2)
        return json_response({})

    result = await BlueskyConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="ejemplo.bsky.social", limit=10
    )
    assert waits == [2.0]
    assert [p.kind for p in result.profiles[0].posts] == ["quote"]
    assert result.warnings == []


async def test_429_over_cap_returns_profile_with_partial_warning(monkeypatch):
    waits = no_sleep(monkeypatch)

    def handler(request):
        if request.url.path.endswith("getProfile"):
            return json_response(PROFILE)
        if request.url.path.endswith("getAuthorFeed"):
            return httpx.Response(429, headers={"Retry-After": "900"})
        return json_response({})

    result = await BlueskyConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="ejemplo.bsky.social", limit=10
    )
    assert waits == []
    (profile,) = result.profiles
    assert profile.posts == []
    assert profile.account.followers == 1500
    assert any("Límite de tasa" in w for w in result.warnings)
