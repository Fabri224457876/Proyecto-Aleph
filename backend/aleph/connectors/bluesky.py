"""Conector Bluesky (AT Protocol) sobre la API pública del AppView (sin autenticación).

Basado en la documentación oficial:
- Lexicones de app.bsky.actor.getProfile, app.bsky.feed.getAuthorFeed y app.bsky.graph.getFollows:
  https://github.com/bluesky-social/atproto/tree/main/lexicons
  (consultados: feed/getAuthorFeed.json, feed/defs.json, actor/defs.json, graph/getFollows.json).
- Guía de la API: https://docs.bsky.app/docs/api/app-bsky-feed-get-author-feed (no se pudo consultar).

Verificado contra los lexicones consultados: feedViewPost (post, reply, reason), postView
(uri, record, author, embed), reasonRepost (by, indexedAt), profileViewDetailed (incluye createdAt,
followersCount, followsCount, indexedAt) y getFollows (limit 1..100, cursor).
Sin verificar en respuestas reales: el $type exacto de reasonRepost (se detecta de forma tolerante)
y la clave de respuesta "followers" de getFollowers (se asume simétrica a getFollows).
"""

from typing import Any, ClassVar

import httpx

from aleph.connectors._util import (
    HttpSession,
    RateLimited,
    clean_handle,
    dedupe,
    empty_result,
    extract_hashtags,
    extract_mentions,
    extract_urls,
    norm_hashtag,
    parse_datetime,
    positive_int,
    rate_limit_warning,
    require_text,
    utcnow,
)
from aleph.connectors.base import Connector, ConnectorError, register
from aleph.core.schemas import AccountProfile, AccountRecord, CollectionResult, PostRecord

API = "https://public.api.bsky.app/xrpc"
PROFILE_URL = "https://bsky.app/profile/{handle}"
PAGE_MAX = 100  # máximo por página en getAuthorFeed y en getFollows/getFollowers


def _not_found(response: httpx.Response) -> bool:
    # El AppView responde HTTP 400 con "Profile not found" para cuentas inexistentes.
    return response.status_code == 400 and "not found" in response.text.lower()


def _inner_record(embed: dict[str, Any]) -> dict[str, Any]:
    """Registro citado dentro de un embed (cita simple o cita con medios), en forma de vista o raw."""
    base = str(embed.get("$type", "")).split("#")[0]
    if base == "app.bsky.embed.record":
        return embed.get("record") or {}
    if base == "app.bsky.embed.recordWithMedia":
        return (embed.get("record") or {}).get("record") or {}
    return {}


def _post_from_item(item: dict[str, Any], seen: set[str]) -> PostRecord | None:
    post_view = item.get("post") or {}
    uri = str(post_view.get("uri") or "")
    if not uri:
        return None

    reason = item.get("reason") or {}
    reason_type = str(reason.get("$type", ""))
    if reason_type.endswith("#reasonPin"):
        return None  # el post fijado ya aparece en su posición normal
    is_repost = reason_type.endswith("#reasonRepost") or (not reason_type and "by" in reason)
    post_id = f"repost:{uri}" if is_repost else uri
    if post_id in seen:
        return None
    seen.add(post_id)

    record = post_view.get("record") or {}
    text = str(record.get("text") or "")

    # Hashtags y enlaces vienen en facets (con el texto ya separado); las menciones sólo traen
    # DID, así que el handle se extrae del texto.
    facets = record.get("facets")
    if isinstance(facets, list):
        features = [feature for facet in facets for feature in facet.get("features", [])]
        hashtags = dedupe(
            norm_hashtag(f.get("tag")) for f in features if str(f.get("$type", "")).endswith("#tag")
        )
        links = [
            str(f["uri"])
            for f in features
            if str(f.get("$type", "")).endswith("#link") and f.get("uri")
        ]
        urls = dedupe(links + extract_urls(text))
    else:
        hashtags = extract_hashtags(text)
        urls = extract_urls(text)

    reply = record.get("reply") or item.get("reply") or {}
    parent = str((reply.get("parent") or {}).get("uri") or "") if reply else ""

    view_inner = _inner_record(post_view.get("embed") or {})
    quoted_uri = str(view_inner.get("uri") or "") or str(
        _inner_record(record.get("embed") or {}).get("uri") or ""
    )
    if "app.bsky.feed.post" not in quoted_uri:
        quoted_uri = ""  # citar listas, feeds o packs no es una cita de publicación
    quoted_handle = str((view_inner.get("author") or {}).get("handle") or "")

    if is_repost:
        kind = "repost"
    elif quoted_uri:
        kind = "quote"
    elif parent:
        kind = "reply"
    else:
        kind = "original"

    author = post_view.get("author") or {}
    meta: dict[str, Any] = {
        "uri": uri,
        "likes": post_view.get("likeCount"),
        "reposts": post_view.get("repostCount"),
        "replies": post_view.get("replyCount"),
        "quotes": post_view.get("quoteCount"),
        "original_author": author.get("handle"),
    }
    if is_repost:
        meta["repost_by"] = (reason.get("by") or {}).get("handle")
    if quoted_uri:
        meta["quoted_uri"] = quoted_uri
        meta["quoted_handle"] = quoted_handle

    created = None
    if is_repost:
        created = parse_datetime(reason.get("indexedAt"))  # cuándo se repostó
    created = created or parse_datetime(record.get("createdAt"))
    created = created or parse_datetime(post_view.get("indexedAt"))

    langs = record.get("langs") or []
    return PostRecord(
        platform_post_id=post_id,
        text=text,
        created_at=created,
        lang=str(langs[0]) if langs else "",
        kind=kind,
        reply_to="" if is_repost else parent,
        mentions=extract_mentions(text),
        hashtags=hashtags,
        urls=urls,
        client="",
        meta=meta,
    )


@register
class BlueskyConnector(Connector):
    name = "bluesky"
    title = "Bluesky (AT Protocol, API pública)"
    mode = "live"
    params: ClassVar[dict[str, str]] = {
        "handle": "Handle o DID de la cuenta, p. ej. bsky.app",
        "limit": "Máximo de publicaciones a recolectar (por defecto 50)",
        "graph_limit": "Máximo de seguidos y de seguidores a listar (por defecto 200)",
    }

    async def collect(
        self, *, handle: str, limit: int = 50, graph_limit: int = 200
    ) -> CollectionResult:
        actor = clean_handle(require_text(handle, "handle"))
        limit = positive_int(limit, "limit", 50)
        graph_limit = positive_int(graph_limit, "graph_limit", 200)
        warnings: list[str] = []
        raw: dict[str, Any] = {"feed": [], "follows": [], "followers": []}

        async with HttpSession(self.client) as http:
            try:
                profile = await http.get_json(
                    f"{API}/app.bsky.actor.getProfile",
                    params={"actor": actor},
                    missing=_not_found,
                )
            except RateLimited as exc:
                return empty_result(
                    self.name, PROFILE_URL.format(handle=actor), [rate_limit_warning(exc)], raw
                )
            if profile is None:
                raise ConnectorError(f"cuenta de Bluesky no encontrada: {actor}")
            raw["profile"] = profile

            posts = await self._posts(http, actor, limit, raw["feed"], warnings)
            following = await self._graph(
                http, "getFollows", "follows", actor, graph_limit, raw["follows"], warnings
            )
            followers = await self._graph(
                http, "getFollowers", "followers", actor, graph_limit, raw["followers"], warnings
            )

        handle_out = str(profile.get("handle") or actor)
        account = AccountRecord(
            platform="bluesky",
            handle=handle_out,
            platform_uid=str(profile.get("did") or ""),
            display_name=str(profile.get("displayName") or ""),
            bio=str(profile.get("description") or ""),
            url=PROFILE_URL.format(handle=handle_out),
            created_at_platform=parse_datetime(profile.get("createdAt")),
            followers=profile.get("followersCount"),
            following=profile.get("followsCount"),
            avatar_url=str(profile.get("avatar") or ""),
            following_handles=following,
            follower_handles=followers,
            meta={
                "posts_count": profile.get("postsCount"),
                "indexed_at": profile.get("indexedAt"),
                "website": profile.get("website"),
            },
        )
        return CollectionResult(
            connector=self.name,
            reference=PROFILE_URL.format(handle=handle_out),
            retrieved_at=utcnow(),
            profiles=[AccountProfile(account=account, posts=posts)],
            warnings=warnings,
            raw=raw,
        )

    async def _posts(
        self, http: HttpSession, actor: str, limit: int, pages: list[Any], warnings: list[str]
    ) -> list[PostRecord]:
        posts: list[PostRecord] = []
        seen: set[str] = set()
        cursor: str | None = None
        try:
            while len(posts) < limit:
                params: dict[str, Any] = {
                    "actor": actor,
                    "limit": min(PAGE_MAX, limit - len(posts)),
                }
                if cursor:
                    params["cursor"] = cursor
                page = await http.get_json(f"{API}/app.bsky.feed.getAuthorFeed", params=params)
                if page is None:
                    break
                pages.append(page)
                items = page.get("feed") or []
                for item in items:
                    post = _post_from_item(item, seen)
                    if post is not None:
                        posts.append(post)
                    if len(posts) >= limit:
                        break
                cursor = page.get("cursor")
                if not cursor or not items:
                    break
        except RateLimited as exc:
            warnings.append(rate_limit_warning(exc))
        except ConnectorError as exc:
            warnings.append(f"publicaciones incompletas: {exc}")
        return posts[:limit]

    async def _graph(
        self,
        http: HttpSession,
        method: str,
        key: str,
        actor: str,
        limit: int,
        pages: list[Any],
        warnings: list[str],
    ) -> list[str]:
        handles: list[str] = []
        cursor: str | None = None
        try:
            while len(handles) < limit:
                params: dict[str, Any] = {
                    "actor": actor,
                    "limit": min(PAGE_MAX, limit - len(handles)),
                }
                if cursor:
                    params["cursor"] = cursor
                page = await http.get_json(f"{API}/app.bsky.graph.{method}", params=params)
                if page is None:
                    break
                pages.append(page)
                items = page.get(key) or []
                handles.extend(str(item["handle"]) for item in items if item.get("handle"))
                cursor = page.get("cursor")
                if not cursor or not items:
                    break
        except RateLimited as exc:
            warnings.append(rate_limit_warning(exc))
        except ConnectorError as exc:
            warnings.append(f"no se pudo listar {key}: {exc}")
        return handles[:limit]
