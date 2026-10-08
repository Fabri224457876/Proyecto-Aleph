"""Mastodon: lookup, statuses paginados por Link, boosts y citas, HTML a texto. Sin red.

Forma de Status según https://docs.joinmastodon.org/entities/Status/ (consultada): content (HTML),
mentions[].acct, tags[].name, application.name, reblog, quote, in_reply_to_id, language, created_at.
"""

from datetime import UTC, datetime

import httpx
import pytest
from connector_helpers import NO_KEYS, client_for, json_response, no_sleep

from aleph.connectors.base import ConnectorError
from aleph.connectors.mastodon import MastodonConnector

INSTANCE = "mastodon.example"
ACCOUNT_ID = "109"
ACCOUNT = {
    "id": ACCOUNT_ID,
    "username": "cuenta_demo",
    "acct": "cuenta_demo",
    "display_name": "Cuenta de prueba",
    "locked": False,
    "bot": False,
    "note": "<p>Cuenta de <b>prueba</b> del fediverso</p>",
    "url": f"https://{INSTANCE}/@cuenta_demo",
    "avatar": "https://files.example/avatar.png",
    "created_at": "2016-03-16T00:00:00.000Z",
    "followers_count": 3,
    "following_count": 1,
    "statuses_count": 4,
    "last_status_at": "2024-06-04",
    "fields": [{"name": "Sitio",
                "value": '<a href="https://sitio.example.org" rel="me">sitio.example.org</a>',
                "verified_at": None}],
}
OWNER = {"id": ACCOUNT_ID, "acct": "cuenta_demo", "username": "cuenta_demo"}

STATUS_1 = {
    "id": "1001",
    "created_at": "2024-06-01T12:00:00.000Z",
    "in_reply_to_id": None,
    "in_reply_to_account_id": None,
    "reblog": None,
    "quote": None,
    "language": "es",
    "url": f"https://{INSTANCE}/@cuenta_demo/1001",
    "uri": f"https://{INSTANCE}/users/cuenta_demo/statuses/1001",
    "visibility": "public",
    "sensitive": False,
    "spoiler_text": "",
    "content": (
        '<p>Hola <span class="h-card"><a href="https://mastodon.example/@bob" '
        'class="u-url mention">@<span>bob</span></a></span> mirá '
        '<a href="https://mastodon.example/tags/CTI" class="mention hashtag" rel="tag">#<span>CTI'
        '</span></a> <a href="https://ejemplo.example/articulo" target="_blank" '
        'rel="nofollow noopener noreferrer"><span class="invisible">https://</span>'
        '<span class="ellipsis">ejemplo.example/articulo</span></a></p>'
    ),
    "mentions": [
        {"id": "5", "username": "bob", "url": f"https://{INSTANCE}/@bob", "acct": "bob"},
        {"id": "6", "username": "carol", "url": "https://otra.example/@carol",
         "acct": "carol@otra.example"},
    ],
    "tags": [{"name": "CTI", "url": f"https://{INSTANCE}/tags/CTI"}],
    "application": {"name": "Web", "website": None},
    "replies_count": 0,
    "reblogs_count": 1,
    "favourites_count": 2,
    "account": OWNER,
}
STATUS_2 = {  # respuesta a STATUS_1, sin aplicación informada
    "id": "1002",
    "created_at": "2024-06-02T08:00:00.000Z",
    "in_reply_to_id": "1001",
    "in_reply_to_account_id": ACCOUNT_ID,
    "reblog": None,
    "quote": None,
    "language": None,
    "content": "<p>Respuesta sin etiquetas</p>",
    "mentions": [],
    "tags": [],
    "application": None,
    "visibility": "public",
    "sensitive": False,
    "spoiler_text": "",
    "account": OWNER,
}
STATUS_3 = {  # boost de otra cuenta: el contenido viene en `reblog`
    "id": "1003",
    "created_at": "2024-06-03T09:00:00.000Z",
    "in_reply_to_id": None,
    "reblog": {
        "id": "900",
        "uri": "https://otra.example/users/zeta/statuses/900",
        "created_at": "2024-05-30T10:00:00.000Z",
        "content": "<p>Contenido original de otro servidor #Remoto</p>",
        "language": "en",
        "mentions": [],
        "tags": [{"name": "Remoto", "url": "https://otra.example/tags/Remoto"}],
        "account": {"id": "77", "acct": "zeta@otra.example", "username": "zeta"},
        "application": {"name": "Otra app"},
    },
    "quote": None,
    "content": "",
    "mentions": [],
    "tags": [],
    "application": {"name": "Web"},
    "language": None,
    "visibility": "public",
    "sensitive": False,
    "spoiler_text": "",
    "account": OWNER,
}
STATUS_4 = {  # cita de STATUS_1
    "id": "1004",
    "created_at": "2024-06-04T10:00:00.000Z",
    "in_reply_to_id": None,
    "reblog": None,
    "quote": {"state": "accepted", "quoted_status": {"id": "1001"}},
    "language": "es",
    "content": "<p>Mirá esto</p>",
    "mentions": [],
    "tags": [],
    "application": {"name": "Web"},
    "visibility": "public",
    "sensitive": False,
    "spoiler_text": "",
    "account": OWNER,
}

NEXT_LINK = (
    f'<https://{INSTANCE}/api/v1/accounts/{ACCOUNT_ID}/statuses?max_id=1001&limit=2>; rel="next"'
)


def _handler(log: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        path = request.url.path
        if path.endswith("/accounts/lookup"):
            if request.url.params.get("acct") == "cuenta_demo":
                return json_response(ACCOUNT)
            return json_response({"error": "Record not found"}, status=404)
        if path.endswith(f"/accounts/{ACCOUNT_ID}/statuses"):
            if "max_id" in request.url.params:
                return json_response([STATUS_3, STATUS_4])
            return json_response([STATUS_1, STATUS_2], headers={"Link": NEXT_LINK})
        if path.endswith(f"/accounts/{ACCOUNT_ID}/following"):
            return json_response([{"acct": "amigo"}, {"acct": "zeta@otra.example"}])
        if path.endswith(f"/accounts/{ACCOUNT_ID}/followers"):
            return json_response([])  # la instancia oculta la lista
        return httpx.Response(404)

    return handler


async def test_collects_account_statuses_pages_and_graph():
    log: list[httpx.Request] = []
    result = await MastodonConnector(client=client_for(_handler(log)), settings=NO_KEYS).collect(
        handle=f"cuenta_demo@{INSTANCE}", limit=10, graph_limit=5
    )
    (profile,) = result.profiles
    acc = profile.account
    assert acc.handle == f"cuenta_demo@{INSTANCE}"
    assert acc.platform == "mastodon"
    assert acc.platform_uid == f"{INSTANCE}:{ACCOUNT_ID}"
    assert acc.display_name == "Cuenta de prueba"
    assert acc.bio == "Cuenta de prueba del fediverso"
    assert acc.followers == 3 and acc.following == 1
    assert acc.following_handles == [f"amigo@{INSTANCE}", "zeta@otra.example"]
    assert acc.follower_handles == []

    posts = profile.posts
    assert [p.kind for p in posts] == ["original", "reply", "repost", "quote"]

    first = posts[0]
    assert first.text == "Hola @bob mirá #CTI ejemplo.example/articulo"  # sin "https://" oculto
    assert first.mentions == [f"bob@{INSTANCE}", "carol@otra.example"]  # local completado
    assert first.hashtags == ["cti"]
    assert first.urls == ["https://ejemplo.example/articulo"]  # enlace expandido, sin menciones
    assert first.client == "Web"
    assert first.lang == "es"
    assert first.created_at == datetime(2024, 6, 1, 12, tzinfo=UTC)

    assert posts[1].reply_to == "1001"
    assert posts[1].client == ""

    boost = posts[2]
    assert boost.hashtags == ["remoto"]  # tags del estado original
    assert boost.lang == "en"
    assert boost.meta["reblog_of"] == "https://otra.example/users/zeta/statuses/900"
    assert boost.meta["reblog_account"] == "zeta@otra.example"

    assert posts[3].meta["quoted_id"] == "1001"
    assert posts[3].meta["quote_state"] == "accepted"

    # Paginación: la primera página con limit, la segunda por la URL de la cabecera Link.
    status_calls = [r for r in log if r.url.path.endswith("/statuses")]
    assert len(status_calls) == 2
    assert status_calls[0].url.params["limit"] == "10"
    assert status_calls[1].url.params["max_id"] == "1001"

    # Entidades: el enlace declarado en el perfil, relacionado a la cuenta.
    assert any(e.label == "https://sitio.example.org" for e in result.entities)
    assert any(r.type == "declares" and r.dst_ref == "url:https://sitio.example.org"
               for r in result.relations)
    # Seguidores declarados pero lista vacía: se avisa.
    assert any("no expone la lista de followers" in w for w in result.warnings)


async def test_unknown_account_raises():
    with pytest.raises(ConnectorError, match="no encontrada"):
        await MastodonConnector(client=client_for(_handler([])), settings=NO_KEYS).collect(
            handle=f"nadie@{INSTANCE}"
        )


async def test_handle_without_instance_is_rejected():
    with pytest.raises(ConnectorError, match="usuario@instancia"):
        await MastodonConnector(client=client_for(_handler([])), settings=NO_KEYS).collect(
            handle="sinarroba"
        )


async def test_429_within_cap_is_retried(monkeypatch):
    waits = no_sleep(monkeypatch)
    hits = {"statuses": 0}

    def handler(request):
        if request.url.path.endswith("/accounts/lookup"):
            return json_response(ACCOUNT)
        if request.url.path.endswith("/statuses"):
            hits["statuses"] += 1
            if hits["statuses"] == 1:
                return httpx.Response(429, headers={"Retry-After": "1"})
            return json_response([STATUS_2])
        return json_response([])

    result = await MastodonConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle=f"cuenta_demo@{INSTANCE}", limit=5
    )
    assert waits == [1.0]
    assert [p.kind for p in result.profiles[0].posts] == ["reply"]


async def test_429_over_cap_returns_profile_with_warning(monkeypatch):
    waits = no_sleep(monkeypatch)

    def handler(request):
        if request.url.path.endswith("/accounts/lookup"):
            return json_response(ACCOUNT)
        if request.url.path.endswith("/statuses"):
            return httpx.Response(429, headers={"Retry-After": "900"})
        return json_response([])

    result = await MastodonConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle=f"cuenta_demo@{INSTANCE}", limit=5
    )
    assert waits == []
    assert result.profiles[0].posts == []
    assert any("Límite de tasa" in w for w in result.warnings)
