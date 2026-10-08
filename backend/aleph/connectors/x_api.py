"""Conector X (Twitter): API oficial v2. Requiere un token Bearer (ALEPH_X_BEARER_TOKEN), de pago.

Basado en la documentación oficial de X (leída a través de búsqueda, no página completa):
- https://docs.x.com/x-api/users/get-posts: línea de tiempo GET /2/users/{id}/tweets; max_results
  por defecto 10 con tope 100; paginación con pagination_token y meta.next_token; meta.result_count;
  entities con hashtags[].tag, mentions[].username y urls[].expanded_url; referenced_tweets con los
  tipos replied_to y quoted. URL base: https://api.x.com/2.
- https://docs.x.com/x-api/fundamentals/pagination: meta.next_token como cursor opaco.

De memoria, no confirmado en la documentación consultada: GET /2/users/by/username/{username} con
user.fields, GET /2/users/{id}/following, y los campos tweet.fields `source`, `lang` y
`conversation_id`. El tipo "retweeted" dentro de referenced_tweets se asume por analogía con
replied_to y quoted. El mínimo de max_results (5) para la línea de tiempo tampoco se confirmó.
"""

import re
from typing import Any, ClassVar

from aleph.connectors._util import (
    HttpSession,
    RateLimited,
    clean_handle,
    dedupe,
    empty_result,
    extract_hashtags,
    extract_mentions,
    extract_urls,
    get_setting,
    norm_hashtag,
    norm_mention,
    parse_datetime,
    positive_int,
    rate_limit_warning,
    require_text,
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

API = "https://api.x.com/2"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
USER_FIELDS = "created_at,description,entities,location,public_metrics,url,verified,protected"
TWEET_FIELDS = (
    "created_at,lang,entities,referenced_tweets,in_reply_to_user_id,conversation_id,"
    "source,public_metrics"
)
TIMELINE_MIN, TIMELINE_MAX = 5, 100
FOLLOW_MAX = 1000


def _tweet_post(tweet: dict[str, Any]) -> PostRecord:
    text = str(tweet.get("text") or "")
    refs = {
        str(r.get("type")): str(r.get("id") or "") for r in tweet.get("referenced_tweets") or []
    }
    if "retweeted" in refs:
        kind = "repost"
    elif "quoted" in refs:
        kind = "quote"
    elif "replied_to" in refs:
        kind = "reply"
    else:
        kind = "original"

    entities = tweet.get("entities")
    if isinstance(entities, dict):
        # Con entities presentes, X ya separó menciones, hashtags y URLs del texto.
        mentions = dedupe(norm_mention(m.get("username")) for m in entities.get("mentions") or [])
        hashtags = dedupe(norm_hashtag(h.get("tag")) for h in entities.get("hashtags") or [])
        urls = dedupe(
            str(u.get("expanded_url") or u.get("url") or "") for u in entities.get("urls") or []
        )
    else:
        mentions = extract_mentions(text)
        hashtags = extract_hashtags(text)
        urls = extract_urls(text)

    lang = str(tweet.get("lang") or "")
    return PostRecord(
        platform_post_id=str(tweet.get("id", "")),
        text=text,
        created_at=parse_datetime(tweet.get("created_at")),
        lang="" if lang == "und" else lang,
        kind=kind,
        reply_to=refs.get("replied_to", ""),
        mentions=mentions,
        hashtags=hashtags,
        urls=[u for u in urls if u],
        client=str(tweet.get("source") or ""),
        meta={
            "public_metrics": tweet.get("public_metrics"),
            "conversation_id": tweet.get("conversation_id"),
            "in_reply_to_user_id": tweet.get("in_reply_to_user_id"),
            "quoted_id": refs.get("quoted", ""),
            "retweeted_id": refs.get("retweeted", ""),
        },
    )


@register
class XApiConnector(Connector):
    name = "x_api"
    title = "X (API oficial v2, requiere token de pago)"
    mode = "live"
    requires: ClassVar[list[str]] = ["x_bearer_token"]
    params: ClassVar[dict[str, str]] = {
        "handle": "Usuario de X, sin @",
        "limit": "Máximo de publicaciones a recolectar (por defecto 100)",
        "graph_limit": "Máximo de seguidos a listar (por defecto 200)",
    }

    async def collect(
        self, *, handle: str, limit: int = 100, graph_limit: int = 200
    ) -> CollectionResult:
        token = get_setting(self.settings, "x_bearer_token")
        if not token:
            raise ConnectorError(
                "x_api requiere un token Bearer de la API oficial de X (ALEPH_X_BEARER_TOKEN). "
                "Es de pago; no se hizo ninguna consulta."
            )
        username = clean_handle(require_text(handle, "handle"))
        if not USERNAME_RE.match(username):
            raise ConnectorError(f"usuario de X inválido: {username!r}")
        limit = positive_int(limit, "limit", 100)
        graph_limit = positive_int(graph_limit, "graph_limit", 200)
        reference = f"https://x.com/{username}"
        headers = {"Authorization": f"Bearer {token}"}  # el token nunca va en la URL ni en errores
        warnings: list[str] = []
        raw: dict[str, Any] = {"user": None, "timeline": [], "following": []}

        async with HttpSession(self.client, headers=headers) as http:
            try:
                found = await http.get_json(
                    f"{API}/users/by/username/{username}", params={"user.fields": USER_FIELDS}
                )
            except RateLimited as exc:
                return empty_result(self.name, reference, [rate_limit_warning(exc)], raw)
            user = (found or {}).get("data") if isinstance(found, dict) else None
            if not user:
                raise ConnectorError(f"usuario de X no encontrado: {username}")
            raw["user"] = found
            user_id = str(user["id"])

            posts = await self._timeline(http, user_id, limit, raw["timeline"], warnings)
            following = await self._following(
                http, user_id, graph_limit, raw["following"], warnings
            )

        metrics = user.get("public_metrics") or {}
        handle_out = str(user.get("username") or username)
        subject_ref = f"account:x:{handle_out.lower()}"
        entities: list[EntityRecord] = [
            EntityRecord(
                type="account",
                label=handle_out,
                props={"platform": "x", "url": reference},
                confidence=1.0,
                ref=subject_ref,
            )
        ]
        relations: list[RelationRecord] = []
        website = ((user.get("entities") or {}).get("url") or {}).get("urls") or []
        if website:
            site = str(website[0].get("expanded_url") or website[0].get("url") or "")
            if site:
                entities.append(
                    EntityRecord(
                        type="url",
                        label=site,
                        props={"source": "x_profile"},
                        confidence=0.8,
                        ref=f"url:{site}",
                    )
                )
                relations.append(
                    RelationRecord(
                        src_ref=subject_ref,
                        dst_ref=f"url:{site}",
                        type="declares",
                        props={"field": "url"},
                        confidence=0.8,
                    )
                )

        account = AccountRecord(
            platform="x",
            handle=handle_out,
            platform_uid=str(user.get("id") or ""),
            display_name=str(user.get("name") or ""),
            bio=str(user.get("description") or ""),
            url=reference,
            created_at_platform=parse_datetime(user.get("created_at")),
            followers=metrics.get("followers_count"),
            following=metrics.get("following_count"),
            avatar_url=str(user.get("profile_image_url") or ""),
            following_handles=following,
            meta={
                "tweet_count": metrics.get("tweet_count"),
                "listed_count": metrics.get("listed_count"),
                "verified": user.get("verified"),
                "protected": user.get("protected"),
                "location": user.get("location"),
            },
        )
        return CollectionResult(
            connector=self.name,
            reference=reference,
            retrieved_at=utcnow(),
            profiles=[AccountProfile(account=account, posts=posts)],
            entities=entities,
            relations=relations,
            warnings=warnings,
            raw=raw,
        )

    async def _timeline(
        self, http: HttpSession, user_id: str, limit: int, pages: list[Any], warnings: list[str]
    ) -> list[PostRecord]:
        posts: list[PostRecord] = []
        seen: set[str] = set()
        token: str | None = None
        try:
            while len(posts) < limit:
                remaining = limit - len(posts)
                params: dict[str, Any] = {
                    "max_results": max(TIMELINE_MIN, min(TIMELINE_MAX, remaining)),
                    "tweet.fields": TWEET_FIELDS,
                }
                if token:
                    params["pagination_token"] = token
                page = await http.get_json(f"{API}/users/{user_id}/tweets", params=params)
                if not isinstance(page, dict):
                    break
                pages.append(page)
                for tweet in page.get("data") or []:
                    tweet_id = str(tweet.get("id", ""))
                    if tweet_id and tweet_id not in seen:
                        seen.add(tweet_id)
                        posts.append(_tweet_post(tweet))
                token = (page.get("meta") or {}).get("next_token")
                if not token:
                    break
        except RateLimited as exc:
            warnings.append(rate_limit_warning(exc))
        except ConnectorError as exc:
            warnings.append(f"publicaciones incompletas: {exc}")
        return posts[:limit]

    async def _following(
        self, http: HttpSession, user_id: str, limit: int, pages: list[Any], warnings: list[str]
    ) -> list[str]:
        handles: list[str] = []
        token: str | None = None
        try:
            while len(handles) < limit:
                params: dict[str, Any] = {
                    "max_results": min(FOLLOW_MAX, max(1, limit - len(handles))),
                }
                if token:
                    params["pagination_token"] = token
                page = await http.get_json(f"{API}/users/{user_id}/following", params=params)
                if not isinstance(page, dict):
                    break
                pages.append(page)
                users = page.get("data") or []
                handles.extend(str(u["username"]) for u in users if u.get("username"))
                token = (page.get("meta") or {}).get("next_token")
                if not token:
                    break
        except RateLimited as exc:
            warnings.append(rate_limit_warning(exc))
        except ConnectorError as exc:
            warnings.append(f"no se pudo listar following: {exc}")
        return handles[:limit]
