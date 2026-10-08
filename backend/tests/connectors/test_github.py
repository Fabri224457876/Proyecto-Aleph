"""GitHub: usuario, repositorios, eventos públicos y emails de commits. Sin red.

Forma del envelope de evento según https://docs.github.com/en/rest/activity/events (consultada).
La lista `payload.commits` de PushEvent NO aparece en esa documentación: el test la modela como
la forma habitual del campo, y el caso sin esa lista se prueba aparte.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from connector_helpers import NO_KEYS, client_for, json_response, no_sleep

from aleph.connectors.base import ConnectorError
from aleph.connectors.github import GithubConnector

USER = {
    "login": "octo-demo",
    "id": 42,
    "name": "Octo Demo",
    "bio": "Desarrollo de prueba",
    "company": "@ExampleOrg",
    "blog": "example.org",
    "email": "octo@example.org",
    "twitter_username": "octo_tw",
    "followers": 10,
    "following": 2,
    "public_repos": 1,
    "public_gists": 0,
    "created_at": "2015-01-01T00:00:00Z",
    "html_url": "https://github.com/octo-demo",
    "avatar_url": "https://avatars.example/42",
    "location": "Ciudad Demo",
    "type": "User",
}
REPOS = [{
    "full_name": "octo-demo/proyecto",
    "html_url": "https://github.com/octo-demo/proyecto",
    "fork": False,
    "language": "Python",
    "stargazers_count": 5,
    "description": "Herramienta de prueba",
    "created_at": "2020-02-02T10:00:00Z",
}]
PUSH = {
    "id": "1001",
    "type": "PushEvent",
    "actor": {"login": "octo-demo"},
    "repo": {"name": "octo-demo/proyecto"},
    "created_at": "2024-03-01T10:00:00Z",
    "payload": {
        "push_id": 1,
        "ref": "refs/heads/main",
        "head": "abc2",
        "before": "abc0",
        "commits": [
            {"sha": "abc1",
             "author": {"email": "42+octo-demo@users.noreply.github.com", "name": "Octo Demo"},
             "message": "Corrige #bug en login (ver @revisor)", "distinct": True},
            {"sha": "abc2", "author": {"email": "octo@example.org", "name": "octo-demo"},
             "message": "Documentación", "distinct": True},
        ],
    },
}
ISSUE_COMMENT = {
    "id": "1002",
    "type": "IssueCommentEvent",
    "actor": {"login": "octo-demo"},
    "repo": {"name": "otro/repo"},
    "created_at": "2024-03-02T11:00:00Z",
    "payload": {
        "action": "created",
        "issue": {"html_url": "https://github.com/otro/repo/issues/7", "number": 7,
                  "title": "Problema"},
        "comment": {"id": 55, "html_url": "https://github.com/otro/repo/issues/7#issuecomment-55",
                    "body": "¿Probaron con https://ejemplo.example/doc ?"},
    },
}
WATCH = {"id": "1003", "type": "WatchEvent", "actor": {"login": "octo-demo"},
         "repo": {"name": "otro/repo"}, "created_at": "2024-03-02T12:00:00Z",
         "payload": {"action": "started"}}
FORK = {
    "id": "1004",
    "type": "ForkEvent",
    "actor": {"login": "octo-demo"},
    "repo": {"name": "base/x"},
    "created_at": "2024-03-03T09:00:00Z",
    "payload": {"forkee": {"full_name": "octo-demo/x",
                           "html_url": "https://github.com/octo-demo/x",
                           "description": "Fork de prueba"}},
}


def _handler(log: list[httpx.Request], events=None):
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        path = request.url.path
        if path == "/users/octo-demo":
            return json_response(USER)
        if path == "/users/octo-demo/repos":
            return json_response(REPOS)
        if path == "/users/octo-demo/events/public":
            if events is not None:
                return json_response(events(request))
            return json_response([PUSH, ISSUE_COMMENT, WATCH, FORK])
        return json_response({"message": "Not Found"}, status=404)

    return handler


async def test_collects_profile_repos_events_and_emails():
    log: list[httpx.Request] = []
    result = await GithubConnector(client=client_for(_handler(log)), settings=NO_KEYS).collect(
        handle="octo-demo", limit=100, repo_limit=100
    )
    (profile,) = result.profiles
    acc = profile.account
    assert acc.platform == "github" and acc.handle == "octo-demo"
    assert acc.platform_uid == "42"
    assert acc.followers == 10 and acc.following == 2
    assert acc.created_at_platform == datetime(2015, 1, 1, tzinfo=UTC)

    # WatchEvent no tiene contenido propio: no se convierte en publicación.
    assert [p.kind for p in profile.posts] == ["original", "reply", "repost"]

    push = profile.posts[0]
    assert push.text == "Corrige #bug en login (ver @revisor)\nDocumentación"
    assert push.hashtags == ["bug"]
    assert push.mentions == ["revisor"]
    assert push.created_at == datetime(2024, 3, 1, 10, tzinfo=UTC)
    assert push.meta["repo"] == "octo-demo/proyecto"

    reply = profile.posts[1]
    assert reply.reply_to == "https://github.com/otro/repo/issues/7"
    assert reply.urls == ["https://ejemplo.example/doc"]

    assert profile.posts[2].meta["forkee"] == "octo-demo/x"

    ents = {e.ref: e for e in result.entities}
    noreply = ents["email:42+octo-demo@users.noreply.github.com"]
    assert noreply.props["noreply"] is True
    assert noreply.confidence == 0.9  # la dirección contiene el login
    # Profile email (0.9) y commit (0.8): gana la confianza mayor.
    assert ents["email:octo@example.org"].confidence == 0.9
    assert ents["url:https://github.com/octo-demo/proyecto"].props["fork"] is False
    assert ents["url:https://example.org"].label == "https://example.org"  # blog sin esquema
    assert ents["organization:exampleorg"].label == "ExampleOrg"
    assert ents["account:x:octo_tw"].props["platform"] == "x"

    rels = {(r.type, r.dst_ref) for r in result.relations}
    assert ("uses_email", "email:42+octo-demo@users.noreply.github.com") in rels
    assert ("owns", "url:https://github.com/octo-demo/proyecto") in rels
    assert ("declares", "url:https://example.org") in rels
    assert all(r.src_ref == "account:github:octo-demo" for r in result.relations)
    assert "account:github:octo-demo" in ents


async def test_events_pages_keep_a_fixed_per_page(monkeypatch):
    log: list[httpx.Request] = []
    filler = [{"id": str(i), "type": "WatchEvent", "actor": {"login": "octo-demo"},
               "repo": {"name": "x/y"}, "created_at": "2024-03-02T12:00:00Z", "payload": {}}
              for i in range(100)]

    def events(request):
        return filler if request.url.params["page"] == "1" else [PUSH]

    await GithubConnector(client=client_for(_handler(log, events)), settings=NO_KEYS).collect(
        handle="octo-demo", limit=150
    )
    event_calls = [r for r in log if r.url.path.endswith("/events/public")]
    assert [c.url.params["page"] for c in event_calls] == ["1", "2"]
    assert all(c.url.params["per_page"] == "100" for c in event_calls)


async def test_push_without_commit_list_is_reported_not_guessed():
    push = {**PUSH, "payload": {"push_id": 9, "ref": "refs/heads/main", "head": "zz"}}
    result = await GithubConnector(
        client=client_for(_handler([], lambda request: [push])), settings=NO_KEYS
    ).collect(handle="octo-demo", limit=10)
    assert not any(e.type == "email" and e.props.get("source") == "github_commit"
                   for e in result.entities)
    assert any("sin lista de commits" in w for w in result.warnings)


async def test_unknown_user_raises():
    with pytest.raises(ConnectorError, match="no encontrado"):
        await GithubConnector(client=client_for(_handler([])), settings=NO_KEYS).collect(
            handle="nadie-aqui"
        )


async def test_invalid_login_is_rejected():
    with pytest.raises(ConnectorError, match="inválido"):
        await GithubConnector(client=client_for(_handler([])), settings=NO_KEYS).collect(
            handle="octo demo!"
        )


async def test_token_is_sent_as_bearer_and_never_returned():
    log: list[httpx.Request] = []
    settings = SimpleNamespace(github_token="tok-secreto-123", x_bearer_token="")
    result = await GithubConnector(client=client_for(_handler(log)), settings=settings).collect(
        handle="octo-demo", limit=10
    )
    assert all(r.headers["authorization"] == "Bearer tok-secreto-123" for r in log)
    assert "tok-secreto-123" not in result.model_dump_json()


async def test_403_primary_limit_within_cap_is_retried(monkeypatch):
    waits = no_sleep(monkeypatch)
    hits = {"user": 0}

    def handler(request):
        if request.url.path == "/users/octo-demo":
            hits["user"] += 1
            if hits["user"] == 1:
                return httpx.Response(403, headers={"x-ratelimit-remaining": "0",
                                                    "retry-after": "2"})
            return json_response(USER)
        return json_response([])

    result = await GithubConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="octo-demo", limit=10
    )
    assert waits == [2.0]
    assert result.profiles[0].account.handle == "octo-demo"


async def test_403_primary_limit_over_cap_returns_warning_only(monkeypatch):
    waits = no_sleep(monkeypatch)
    import time

    def handler(request):
        reset = str(int(time.time()) + 3600)
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0",
                                            "x-ratelimit-reset": reset})

    result = await GithubConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="octo-demo", limit=10
    )
    assert waits == []
    assert result.profiles == []
    assert any("Límite de tasa" in w for w in result.warnings)
