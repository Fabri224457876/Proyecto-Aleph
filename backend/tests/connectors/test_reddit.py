"""Reddit: about, submitted y comments con paginación por `after`. Sin red.

Formas de listado y de `about` basadas en memoria de la API pública de Reddit; no verificadas contra
la documentación en vivo (el sitio no era accesible desde el entorno de desarrollo).
"""

from datetime import UTC, datetime

import httpx
import pytest
from connector_helpers import NO_KEYS, client_for, json_response, no_sleep

from aleph.connectors.base import ConnectorError
from aleph.connectors.reddit import RedditConnector

ABOUT = {
    "kind": "t2",
    "data": {
        "name": "ejemplo_usuario",
        "id": "abc123",
        "created_utc": 1500000000.0,  # 2017-07-14T02:40:00Z
        "link_karma": 10,
        "comment_karma": 20,
        "total_karma": 30,
        "is_gold": False,
        "icon_img": "https://styles.example/icon.png?a=1&b=2",
        "subreddit": {"public_description": "Bio de prueba", "display_name": "u_ejemplo_usuario"},
    },
}

SUBMITTED_1 = {
    "kind": "Listing",
    "data": {
        "after": "t3_p2",
        "before": None,
        "children": [
            {"kind": "t3", "data": {
                "name": "t3_p1", "id": "p1", "title": "Título del post",
                "selftext": "Mirá u/Otro_User y https://enlace.example/x", "is_self": True,
                "url": "https://www.reddit.com/r/test/comments/p1/titulo/",
                "subreddit": "test", "permalink": "/r/test/comments/p1/titulo/",
                "created_utc": 1700000000.0, "score": 3, "num_comments": 1, "over_18": False,
                "crosspost_parent_list": []}},
            {"kind": "t3", "data": {
                "name": "t3_p2", "id": "p2", "title": "Enlace compartido", "selftext": "",
                "is_self": False, "url": "https://externo.example/nota",
                "subreddit": "noticias", "permalink": "/r/noticias/comments/p2/enlace/",
                "created_utc": 1700000500.0, "score": 10, "num_comments": 0, "over_18": False,
                "crosspost_parent_list": [{"subreddit": "origen", "name": "t3_orig"}]}},
        ],
    },
}
SUBMITTED_2 = {
    "kind": "Listing",
    "data": {
        "after": None,
        "before": None,
        "children": [
            {"kind": "t3", "data": {
                "name": "t3_p3", "id": "p3", "title": "Tercero", "selftext": "",
                "is_self": True, "url": "https://www.reddit.com/r/test/comments/p3/",
                "subreddit": "test", "permalink": "/r/test/comments/p3/",
                "created_utc": 1700001000.0, "score": 1, "num_comments": 0, "over_18": False,
                "crosspost_parent_list": []}},
        ],
    },
}
COMMENTS_1 = {
    "kind": "Listing",
    "data": {
        "after": None,
        "before": None,
        "children": [
            {"kind": "t1", "data": {
                "name": "t1_c1", "id": "c1", "body": "Respuesta con #Tag y u/Otro_User",
                "parent_id": "t3_p1", "link_id": "t3_p1", "link_title": "Título del post",
                "subreddit": "test", "permalink": "/r/test/comments/p1/titulo/c1/",
                "created_utc": 1700000100.0, "score": 2}},
        ],
    },
}


def _handler(log: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        path = request.url.path
        if path.endswith("/user/ejemplo_usuario/about.json"):
            return json_response(ABOUT)
        if path.endswith("/user/ejemplo_usuario/submitted.json"):
            if request.url.params.get("after") == "t3_p2":
                return json_response(SUBMITTED_2)
            return json_response(SUBMITTED_1)
        if path.endswith("/user/ejemplo_usuario/comments.json"):
            return json_response(COMMENTS_1)
        return json_response({"message": "Not Found", "error": 404}, status=404)

    return handler


async def test_collects_about_submissions_pages_and_comments():
    log: list[httpx.Request] = []
    result = await RedditConnector(client=client_for(_handler(log)), settings=NO_KEYS).collect(
        handle="u/ejemplo_usuario", limit=100
    )
    (profile,) = result.profiles
    acc = profile.account
    assert acc.platform == "reddit" and acc.handle == "ejemplo_usuario"
    assert acc.platform_uid == "abc123"
    assert acc.bio == "Bio de prueba"
    assert acc.created_at_platform == datetime(2017, 7, 14, 2, 40, tzinfo=UTC)
    assert acc.meta["link_karma"] == 10

    posts = profile.posts
    assert [p.kind for p in posts] == ["original", "repost", "original", "reply"]

    first = posts[0]
    assert first.platform_post_id == "t3_p1"
    assert first.text == "Título del post\n\nMirá u/Otro_User y https://enlace.example/x"
    assert first.mentions == ["otro_user"]
    assert first.urls == ["https://enlace.example/x"]
    assert first.created_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)

    crosspost = posts[1]
    assert crosspost.meta["crosspost_from"] == "origen"
    assert crosspost.urls == ["https://externo.example/nota"]

    comment = posts[3]
    assert comment.reply_to == "t3_p1"
    assert comment.mentions == ["otro_user"]
    assert comment.hashtags == ["tag"]
    assert comment.urls == []

    # Paginación de submitted: la segunda página va con el `after` de la primera.
    submitted = [r for r in log if r.url.path.endswith("submitted.json")]
    assert len(submitted) == 2
    assert submitted[1].url.params["after"] == "t3_p2"
    # Todas las llamadas piden raw_json=1 y llevan un User-Agent identificable.
    assert all(r.url.params.get("raw_json") == "1" for r in log)
    assert all(r.headers["user-agent"].startswith("linux:aleph-osint") for r in log)


async def test_unknown_user_raises():
    with pytest.raises(ConnectorError, match="no encontrado"):
        await RedditConnector(client=client_for(_handler([])), settings=NO_KEYS).collect(
            handle="nadie_aqui"
        )


async def test_suspended_account_has_no_posts_and_a_warning():
    def handler(request):
        if request.url.path.endswith("/about.json"):
            return json_response({"kind": "t2", "data": {"is_suspended": True, "name": "susp"}})
        raise AssertionError("una cuenta suspendida no debe listar publicaciones")

    result = await RedditConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="susp"
    )
    assert result.profiles[0].posts == []
    assert result.profiles[0].account.meta["is_suspended"] is True
    assert any("suspendida" in w for w in result.warnings)


async def test_invalid_username_is_rejected():
    with pytest.raises(ConnectorError, match="inválido"):
        await RedditConnector(client=client_for(_handler([])), settings=NO_KEYS).collect(
            handle="no es válido!"
        )


async def test_429_within_cap_is_retried(monkeypatch):
    waits = no_sleep(monkeypatch)
    hits = {"about": 0}

    def handler(request):
        if request.url.path.endswith("/about.json"):
            hits["about"] += 1
            if hits["about"] == 1:
                return httpx.Response(429, headers={"Retry-After": "1"})
            return json_response(ABOUT)
        return json_response({"kind": "Listing", "data": {"after": None, "children": []}})

    result = await RedditConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="ejemplo_usuario"
    )
    assert waits == [1.0]
    assert result.profiles[0].account.handle == "ejemplo_usuario"


async def test_429_over_cap_returns_partial_result(monkeypatch):
    waits = no_sleep(monkeypatch)

    def handler(request):
        if request.url.path.endswith("/about.json"):
            return json_response(ABOUT)
        return httpx.Response(429, headers={"Retry-After": "900"})

    result = await RedditConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="ejemplo_usuario"
    )
    assert waits == []
    assert result.profiles[0].posts == []
    assert any("Límite de tasa" in w for w in result.warnings)
