"""Conector Reddit: endpoints JSON públicos del perfil de un usuario, sin autenticación.

Basado en la documentación oficial de la API de Reddit:
- https://www.reddit.com/dev/api/ (endpoints de usuario: /user/{name}/about, /submitted y /comments;
  limit máximo 100; paginación con `after`; `raw_json=1` para recibir texto sin entidades HTML).
  NO se pudo consultar desde el entorno de desarrollo (el sitio estaba bloqueado).

Por lo tanto, la forma de los listados (data.children[].data, data.after) y de `about`
(data.subreddit.public_description, data.is_suspended) está basada en memoria y NO verificada.
Como pide la política de la API, se envía un User-Agent identificable.
"""

import re
from typing import Any, ClassVar

from aleph.connectors._util import (
    HttpSession,
    RateLimited,
    dedupe,
    empty_result,
    extract_hashtags,
    extract_reddit_mentions,
    extract_urls,
    parse_datetime,
    positive_int,
    rate_limit_warning,
    require_text,
    utcnow,
)
from aleph.connectors.base import Connector, ConnectorError, register
from aleph.core.schemas import AccountProfile, AccountRecord, CollectionResult, PostRecord

REDDIT_BASE = "https://www.reddit.com"
USER_AGENT_REDDIT = (
    "linux:aleph-osint:0.1.0 (OSINT/CTI research; por el operador de la instancia)"
)
USER_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
PAGE_MAX = 100


def _clean_user(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(https?://)?(www\.|old\.)?reddit\.com/", "", text).lstrip("/")
    lowered = text.lower()
    for prefix in ("user/", "u/"):
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.lstrip("@").strip("/")


def _permalink(path: Any) -> str:
    return f"{REDDIT_BASE}{path}" if path else ""


def _submission_post(data: dict[str, Any]) -> PostRecord:
    title = str(data.get("title") or "")
    selftext = str(data.get("selftext") or "")
    text = f"{title}\n\n{selftext}" if selftext else title
    # En un post de enlace, `url` es el destino externo; en un post de texto apunta a Reddit.
    link = [] if data.get("is_self") else [str(data.get("url") or "")]
    urls = dedupe([u for u in link if u] + extract_urls(selftext))
    crosspost = data.get("crosspost_parent_list") or []
    return PostRecord(
        platform_post_id=str(data.get("name") or f"t3_{data.get('id', '')}"),
        text=text,
        created_at=parse_datetime(data.get("created_utc")),
        kind="repost" if crosspost else "original",
        mentions=extract_reddit_mentions(text),
        hashtags=extract_hashtags(text),
        urls=urls,
        meta={
            "subreddit": data.get("subreddit"),
            "permalink": _permalink(data.get("permalink")),
            "score": data.get("score"),
            "num_comments": data.get("num_comments"),
            "over_18": data.get("over_18"),
            "crosspost_from": crosspost[0].get("subreddit") if crosspost else None,
        },
    )


def _comment_post(data: dict[str, Any]) -> PostRecord:
    body = str(data.get("body") or "")
    return PostRecord(
        platform_post_id=str(data.get("name") or f"t1_{data.get('id', '')}"),
        text=body,
        created_at=parse_datetime(data.get("created_utc")),
        kind="reply",
        reply_to=str(data.get("parent_id") or ""),  # t1_... (comentario) o t3_... (post)
        mentions=extract_reddit_mentions(body),
        hashtags=extract_hashtags(body),
        urls=extract_urls(body),
        meta={
            "subreddit": data.get("subreddit"),
            "link_id": data.get("link_id"),
            "link_title": data.get("link_title"),
            "permalink": _permalink(data.get("permalink")),
            "score": data.get("score"),
        },
    )


@register
class RedditConnector(Connector):
    name = "reddit"
    title = "Reddit (perfil público de usuario)"
    mode = "live"
    params: ClassVar[dict[str, str]] = {
        "handle": "Usuario de Reddit, sin u/ (p. ej. spez)",
        "limit": "Máximo de publicaciones y de comentarios por listado (por defecto 100)",
    }

    async def collect(self, *, handle: str, limit: int = 100) -> CollectionResult:
        name = _clean_user(require_text(handle, "handle"))
        if not USER_RE.match(name):
            raise ConnectorError(
                "usuario de Reddit inválido (3 a 20 caracteres: letras, números, _ y -)"
            )
        limit = positive_int(limit, "limit", 100)
        reference = f"{REDDIT_BASE}/user/{name}"
        warnings: list[str] = []
        raw: dict[str, Any] = {"about": None, "submitted": [], "comments": []}

        async with HttpSession(self.client, headers={"User-Agent": USER_AGENT_REDDIT}) as http:
            try:
                about = await http.get_json(
                    f"{REDDIT_BASE}/user/{name}/about.json", params={"raw_json": 1}
                )
            except RateLimited as exc:
                return empty_result(self.name, reference, [rate_limit_warning(exc)], raw)
            if about is None:
                raise ConnectorError(f"usuario de Reddit no encontrado: {name}")
            raw["about"] = about
            data = about.get("data") or {}

            posts: list[PostRecord] = []
            if data.get("is_suspended"):
                warnings.append("la cuenta está suspendida: no hay publicaciones para recolectar")
            else:
                submissions = await self._listing(
                    http, name, "submitted", limit, raw["submitted"], warnings
                )
                comments = await self._listing(
                    http, name, "comments", limit, raw["comments"], warnings
                )
                posts = [_submission_post(d) for d in submissions]
                posts += [_comment_post(d) for d in comments]

        account = AccountRecord(
            platform="reddit",
            handle=str(data.get("name") or name),
            platform_uid=str(data.get("id") or ""),
            bio=str((data.get("subreddit") or {}).get("public_description") or ""),
            url=reference,
            created_at_platform=parse_datetime(data.get("created_utc")),
            avatar_url=str(data.get("icon_img") or ""),
            meta={
                "link_karma": data.get("link_karma"),
                "comment_karma": data.get("comment_karma"),
                "total_karma": data.get("total_karma"),
                "is_gold": data.get("is_gold"),
                "is_suspended": bool(data.get("is_suspended")),
            },
        )
        return CollectionResult(
            connector=self.name,
            reference=reference,
            retrieved_at=utcnow(),
            profiles=[AccountProfile(account=account, posts=posts)],
            warnings=warnings,
            raw=raw,
        )

    async def _listing(
        self,
        http: HttpSession,
        name: str,
        where: str,
        limit: int,
        pages: list[Any],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        after: str | None = None
        try:
            while len(items) < limit:
                params: dict[str, Any] = {
                    "limit": min(PAGE_MAX, limit - len(items)),
                    "raw_json": 1,
                }
                if after:
                    params["after"] = after
                page = await http.get_json(f"{REDDIT_BASE}/user/{name}/{where}.json", params=params)
                if page is None:
                    break
                pages.append(page)
                listing = page.get("data") or {}
                children = [child.get("data") or {} for child in listing.get("children") or []]
                items.extend(children)
                after = listing.get("after")
                if not after or not children:
                    break
        except RateLimited as exc:
            warnings.append(rate_limit_warning(exc))
        except ConnectorError as exc:
            warnings.append(f"{where}: lectura incompleta ({exc})")
        return items[:limit]
