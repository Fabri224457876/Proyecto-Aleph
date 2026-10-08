"""Conector GitHub: API REST oficial (usuario, repositorios y eventos públicos).

Basado en la documentación oficial de GitHub:
- https://docs.github.com/en/rest/activity/events (consultado: GET /users/{username}/events/public;
  per_page máximo 100 (por defecto 30); hasta 300 eventos de los últimos 30 días; envelope de
  evento: id, type, actor, repo, payload, public, created_at; nombres de los tipos de evento).
- https://docs.github.com/en/rest/users/users y .../repos/repos (de memoria, no consultado:
  GET /users/{username} y GET /users/{username}/repos).
- https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api (de memoria: el
  límite primario responde 403 o 429 con x-ratelimit-remaining: 0 y reinicio en x-ratelimit-reset).

IMPORTANTE, sin verificar: la documentación de eventos consultada describe PushEvent sólo con
repository_id, push_id, ref, head y before; NO documenta el array payload.commits (con author.email
y message). El conector lo lee si llega. Si hay pushes sin esa lista, no obtiene emails y lo avisa
como warning. Obtener emails por commit exigiría una consulta extra por commit, que no se hace.
"""

import re
from typing import Any, ClassVar

import httpx

from aleph.connectors._util import (
    HttpSession,
    RateLimited,
    clean_handle,
    empty_result,
    extract_hashtags,
    extract_mentions,
    extract_urls,
    get_setting,
    join_text,
    parse_datetime,
    positive_int,
    rate_limit_warning,
    require_text,
    retry_after_seconds,
    utcnow,
)
from aleph.connectors.base import Connector, ConnectorError, register
from aleph.core.schemas import (
    AccountProfile,
    AccountRecord,
    CollectionResult,
    EntityRecord,
    PostRecord,
    RelationRecord,
)

API = "https://api.github.com"
PAGE_MAX = 100
EVENT_MAX_PAGES = 3  # GitHub expone como máximo 300 eventos públicos (3 páginas de 100)
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def _rate_hook(response: httpx.Response) -> float | None:
    """GitHub informa el límite primario con 403 y x-ratelimit-remaining: 0 (no siempre con 429)."""
    if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
        return retry_after_seconds(response)
    return None


def _event_post(
    event: dict[str, Any], etype: str, payload: dict[str, Any], repo: str
) -> PostRecord | None:
    """Publicación normalizada de un evento. Devuelve None para eventos sin contenido propio."""
    kind, reply_to, text = "original", "", ""
    extra: dict[str, Any] = {}

    if etype == "PushEvent":
        messages = [str(c.get("message") or "") for c in payload.get("commits") or []]
        text = "\n".join(m for m in messages if m)
        extra = {"ref": payload.get("ref"), "head": payload.get("head")}
    elif etype == "CreateEvent":
        text = str(payload.get("description") or "")
        extra = {"ref_type": payload.get("ref_type"), "ref": payload.get("ref")}
    elif etype in ("IssuesEvent", "PullRequestEvent"):
        item = payload.get("issue") if etype == "IssuesEvent" else payload.get("pull_request")
        item = item or {}
        text = join_text(item.get("title"), item.get("body"))
        extra = {"action": payload.get("action"), "url": item.get("html_url")}
    elif etype == "IssueCommentEvent":
        comment = payload.get("comment") or {}
        kind = "reply"
        reply_to = str((payload.get("issue") or {}).get("html_url") or "")
        text = str(comment.get("body") or "")
        extra = {"url": comment.get("html_url")}
    elif etype in ("PullRequestReviewEvent", "PullRequestReviewCommentEvent"):
        key = "review" if etype == "PullRequestReviewEvent" else "comment"
        content = payload.get(key) or {}
        kind = "reply"
        reply_to = str((payload.get("pull_request") or {}).get("html_url") or "")
        text = str(content.get("body") or "")
        extra = {"url": content.get("html_url"), "state": content.get("state")}
    elif etype == "CommitCommentEvent":
        comment = payload.get("comment") or {}
        kind = "reply"
        reply_to = str(comment.get("commit_id") or "")
        text = str(comment.get("body") or "")
        extra = {"url": comment.get("html_url")}
    elif etype == "ReleaseEvent":
        release = payload.get("release") or {}
        text = join_text(release.get("name") or release.get("tag_name"), release.get("body"))
        extra = {"action": payload.get("action"), "url": release.get("html_url")}
    elif etype == "ForkEvent":
        forkee = payload.get("forkee") or {}
        kind = "repost"  # un fork replica contenido de otro repositorio
        text = str(forkee.get("description") or "")
        extra = {"forkee": forkee.get("full_name"), "url": forkee.get("html_url")}
    else:
        return None  # WatchEvent, MemberEvent, DeleteEvent, etc.: no tienen contenido propio

    return PostRecord(
        platform_post_id=str(event.get("id") or ""),
        text=text,
        created_at=parse_datetime(event.get("created_at")),
        kind=kind,
        reply_to=reply_to,
        mentions=extract_mentions(text),
        hashtags=extract_hashtags(text),
        urls=extract_urls(text),
        client="",
        meta={"event_type": etype, "repo": repo, **extra},
    )


def _record_commit_emails(
    commits: list[Any], login: str, repo: str, hits: dict[str, dict[str, Any]]
) -> None:
    """Acumula emails de autor de commits. La confianza depende de cuánto se parece al login."""
    for commit in commits:
        author = (commit or {}).get("author") or {}
        email = str(author.get("email") or "").strip().lower()
        if "@" not in email:
            continue
        name = str(author.get("name") or "")
        noreply = email.endswith("@users.noreply.github.com")
        local = email.split("@", 1)[0]
        if noreply and (local == login or local.endswith(f"+{login}")):
            confidence = 0.9  # dirección de noreply que contiene el login
        elif name.strip().lower() == login:
            confidence = 0.8
        else:
            confidence = 0.6  # puede ser un commit de otra persona empujado por el usuario
        hit = hits.setdefault(
            email,
            {"confidence": 0.0, "commits": 0, "name": name, "repo": repo, "noreply": noreply},
        )
        hit["commits"] += 1
        hit["confidence"] = max(hit["confidence"], confidence)


def _add(entities: dict[str, EntityRecord], entity: EntityRecord) -> None:
    entities.setdefault(entity.ref, entity)


@register
class GithubConnector(Connector):
    name = "github"
    title = "GitHub (API REST oficial)"
    mode = "live"
    params: ClassVar[dict[str, str]] = {
        "handle": "Login de GitHub, p. ej. octocat",
        "limit": "Máximo de eventos públicos a recolectar (por defecto 100; GitHub expone 300)",
        "repo_limit": "Máximo de repositorios a listar (por defecto 100)",
    }

    async def collect(
        self, *, handle: str, limit: int = 100, repo_limit: int = 100
    ) -> CollectionResult:
        login = clean_handle(require_text(handle, "handle"))
        if not LOGIN_RE.match(login):
            raise ConnectorError(f"login de GitHub inválido: {login!r}")
        limit = positive_int(limit, "limit", 100)
        repo_limit = positive_int(repo_limit, "repo_limit", 100)

        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = get_setting(self.settings, "github_token")  # opcional: sube el límite de consultas
        if token:
            headers["Authorization"] = f"Bearer {token}"

        reference = f"https://github.com/{login}"
        warnings: list[str] = []
        raw: dict[str, Any] = {"user": None, "repos": [], "events": []}

        async with HttpSession(self.client, headers=headers, rate_hook=_rate_hook) as http:
            try:
                user = await http.get_json(f"{API}/users/{login}")
            except RateLimited as exc:
                return empty_result(self.name, reference, [rate_limit_warning(exc)], raw)
            if user is None:
                raise ConnectorError(f"usuario de GitHub no encontrado: {login}")
            raw["user"] = user
            repos = await self._paged(
                http, f"{API}/users/{login}/repos", repo_limit, raw["repos"], warnings, "repos"
            )
            events = await self._paged(
                http,
                f"{API}/users/{login}/events/public",
                limit,
                raw["events"],
                warnings,
                "eventos",
                max_pages=EVENT_MAX_PAGES,
            )

        subject_ref = f"account:github:{login.lower()}"
        posts: list[PostRecord] = []
        commit_hits: dict[str, dict[str, Any]] = {}
        pushes_without_commits = 0
        for event in events:
            etype = str(event.get("type") or "")
            payload = event.get("payload") or {}
            repo_name = str((event.get("repo") or {}).get("name") or "")
            if etype == "PushEvent":
                commits = payload.get("commits")
                if isinstance(commits, list):
                    _record_commit_emails(commits, login.lower(), repo_name, commit_hits)
                else:
                    pushes_without_commits += 1
            post = _event_post(event, etype, payload, repo_name)
            if post is not None:
                posts.append(post)
        if pushes_without_commits and not commit_hits:
            warnings.append(
                f"{pushes_without_commits} PushEvent sin lista de commits: no se obtuvieron emails "
                "de commits (la documentación consultada no describe ese campo)."
            )

        ents: dict[str, EntityRecord] = {}
        relations: list[RelationRecord] = []
        _add(
            ents,
            EntityRecord(
                type="account",
                label=str(user.get("login") or login),
                props={"platform": "github", "url": reference},
                confidence=1.0,
                ref=subject_ref,
            ),
        )

        def declare(entity: EntityRecord, field: str, confidence: float) -> None:
            _add(ents, entity)
            relations.append(
                RelationRecord(
                    src_ref=subject_ref,
                    dst_ref=entity.ref,
                    type="declares",
                    props={"field": field},
                    confidence=confidence,
                )
            )

        public_email = str(user.get("email") or "").strip().lower()
        if "@" in public_email:
            declare(
                EntityRecord(
                    type="email",
                    label=public_email,
                    props={"source": "github_profile"},
                    confidence=0.9,
                    ref=f"email:{public_email}",
                ),
                "email",
                0.9,
            )

        for email, hit in commit_hits.items():
            _add(
                ents,
                EntityRecord(
                    type="email",
                    label=email,
                    props={
                        "source": "github_commit",
                        "noreply": hit["noreply"],
                        "commit_author_name": hit["name"],
                        "repo": hit["repo"],
                    },
                    confidence=hit["confidence"],
                    ref=f"email:{email}",
                ),
            )
            relations.append(
                RelationRecord(
                    src_ref=subject_ref,
                    dst_ref=f"email:{email}",
                    type="uses_email",
                    props={"source": "github_commit", "commits": hit["commits"]},
                    confidence=hit["confidence"],
                )
            )

        for repo in repos:
            url = str(repo.get("html_url") or "")
            if not url:
                continue
            created = parse_datetime(repo.get("created_at"))
            _add(
                ents,
                EntityRecord(
                    type="url",
                    label=url,
                    props={
                        "kind": "github_repo",
                        "full_name": repo.get("full_name"),
                        "fork": bool(repo.get("fork")),
                        "language": repo.get("language"),
                        "stars": repo.get("stargazers_count"),
                        "description": repo.get("description") or "",
                        "created_at": created.isoformat() if created else None,
                    },
                    confidence=1.0,
                    ref=f"url:{url}",
                ),
            )
            relations.append(
                RelationRecord(
                    src_ref=subject_ref,
                    dst_ref=f"url:{url}",
                    type="owns",
                    props={"fork": bool(repo.get("fork"))},
                    confidence=1.0,
                )
            )

        blog = str(user.get("blog") or "").strip()
        if blog:
            url = blog if blog.startswith(("http://", "https://")) else f"https://{blog}"
            declare(
                EntityRecord(
                    type="url",
                    label=url,
                    props={"source": "github_profile"},
                    confidence=0.8,
                    ref=f"url:{url}",
                ),
                "blog",
                0.8,
            )

        company = str(user.get("company") or "").strip().lstrip("@").strip()
        if company:
            declare(
                EntityRecord(
                    type="organization",
                    label=company,
                    props={"source": "github_profile"},
                    confidence=0.5,
                    ref=f"organization:{company.lower()}",
                ),
                "company",
                0.5,
            )

        twitter = str(user.get("twitter_username") or "").strip().lstrip("@")
        if twitter:
            declare(
                EntityRecord(
                    type="account",
                    label=twitter,
                    props={"platform": "x", "source": "github_profile"},
                    confidence=0.8,
                    ref=f"account:x:{twitter.lower()}",
                ),
                "twitter_username",
                0.8,
            )

        account = AccountRecord(
            platform="github",
            handle=str(user.get("login") or login),
            platform_uid=str(user.get("id") or ""),
            display_name=str(user.get("name") or user.get("login") or login),
            bio=str(user.get("bio") or ""),
            url=str(user.get("html_url") or reference),
            created_at_platform=parse_datetime(user.get("created_at")),
            followers=user.get("followers"),
            following=user.get("following"),
            avatar_url=str(user.get("avatar_url") or ""),
            meta={
                "public_repos": user.get("public_repos"),
                "public_gists": user.get("public_gists"),
                "location": user.get("location"),
                "type": user.get("type"),
            },
        )
        return CollectionResult(
            connector=self.name,
            reference=reference,
            retrieved_at=utcnow(),
            profiles=[AccountProfile(account=account, posts=posts)],
            entities=list(ents.values()),
            relations=relations,
            warnings=warnings,
            raw=raw,
        )

    async def _paged(
        self,
        http: HttpSession,
        url: str,
        limit: int,
        pages: list[Any],
        warnings: list[str],
        label: str,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """Paginación por número de página. per_page se mantiene fijo para no saltar elementos."""
        per_page = min(PAGE_MAX, limit)
        items: list[dict[str, Any]] = []
        page_no = 1
        try:
            while len(items) < limit and page_no <= max_pages:
                page = await http.get_json(url, params={"per_page": per_page, "page": page_no})
                if not page:
                    break
                pages.append(page)
                items.extend(page)
                if len(page) < per_page:
                    break
                page_no += 1
        except RateLimited as exc:
            warnings.append(rate_limit_warning(exc))
        except ConnectorError as exc:
            warnings.append(f"{label}: lectura incompleta ({exc})")
        return items[:limit]
