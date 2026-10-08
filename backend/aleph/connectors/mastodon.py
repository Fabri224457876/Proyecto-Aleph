"""Conector Mastodon (fediverso) sobre la API pública de una instancia (sin autenticación).

Basado en la documentación oficial de Mastodon:
- https://docs.joinmastodon.org/entities/Status/ (consultado: campos id, uri, created_at, account,
  content, application, mentions, tags, language, in_reply_to_id, reblog, quote, quote_approval).
  Confirmado: existe `quote` (Quote, ShallowQuote o null) y no existe `quote_id`.
- https://docs.joinmastodon.org/methods/accounts/ (consultado: lookup con acct y 404 "Record not
  found"; statuses con limit por defecto 20 y máximo 40; following y followers con limit máximo 80;
  paginación con la cabecera Link rel="next").
- https://docs.joinmastodon.org/entities/Account/ (consultado: acct, username, id, display_name,
  note, url, avatar, created_at, followers_count, following_count, statuses_count, last_status_at,
  fields, locked, bot).

Sin verificar en respuestas reales: la estructura interna de `quote` (sólo se usa `state` y
`quoted_status.id`/`quoted_status_id` como meta). La documentación no dice qué devuelven las listas
de seguidos y seguidores cuando la cuenta las oculta: el conector asume lista vacía y lo avisa.
"""

import re
from typing import Any, ClassVar

from aleph.connectors._util import (
    HttpSession,
    RateLimited,
    anchor_hrefs,
    dedupe,
    empty_result,
    html_to_text,
    norm_hashtag,
    parse_datetime,
    parse_html,
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

_INSTANCE_RE = re.compile(r"^[a-z0-9.-]+(?::\d{1,5})?$")
STATUS_PAGE_MAX = 40  # máximo de statuses por página
GRAPH_PAGE_MAX = 80  # máximo de cuentas por página en following y followers


def _split_account(value: str) -> tuple[str, str]:
    text = str(value or "").strip().lstrip("@")
    if "@" not in text:
        raise ConnectorError(
            "indicá la cuenta como usuario@instancia, p. ej. Mastodon@mastodon.social"
        )
    user, instance = text.rsplit("@", 1)
    instance = instance.lower()
    if not user or not _INSTANCE_RE.match(instance):
        raise ConnectorError(f"instancia inválida: {instance!r}")
    return user, instance


def _home_of(acct: str, instance: str) -> str:
    """Instancia donde vive una cuenta: la del acct si es remoto, o `instance` si es local."""
    return acct.split("@", 1)[1].lower() if "@" in acct else instance


def _full_acct(acct: Any, home: str) -> str:
    """Handle completo en minúscula: usuario@instancia (los acct locales se completan con `home`)."""
    text = str(acct or "").strip().lstrip("@").lower()
    if not text:
        return ""
    return text if "@" in text else f"{text}@{home}"


def _post(status: dict[str, Any], instance: str) -> PostRecord:
    reblog = status.get("reblog") if isinstance(status.get("reblog"), dict) else None
    source = reblog or status  # un boost trae el contenido en `reblog`
    owner = source.get("account") or {}
    home = _home_of(str(owner.get("acct") or ""), instance)

    root = parse_html(str(source.get("content") or ""))
    text = root.text(skip_classes=("invisible",))  # los "invisible" son partes ocultas de URLs
    urls = anchor_hrefs(root, exclude_classes=("mention", "hashtag"))
    mentions = dedupe(_full_acct(m.get("acct"), home) for m in source.get("mentions") or [])
    hashtags = dedupe(norm_hashtag(t.get("name")) for t in source.get("tags") or [])

    quote = status.get("quote") if isinstance(status.get("quote"), dict) else None
    if reblog is not None:
        kind = "repost"
    elif quote:
        kind = "quote"
    elif status.get("in_reply_to_id"):
        kind = "reply"
    else:
        kind = "original"

    meta: dict[str, Any] = {
        "url": status.get("url") or "",
        "visibility": status.get("visibility"),
        "sensitive": status.get("sensitive"),
        "spoiler_text": status.get("spoiler_text") or "",
        "replies": status.get("replies_count"),
        "reblogs": status.get("reblogs_count"),
        "favourites": status.get("favourites_count"),
        "edited_at": status.get("edited_at"),
    }
    if reblog is not None:
        meta["reblog_of"] = reblog.get("uri") or ""
        meta["reblog_account"] = owner.get("acct") or ""
    if quote:
        quoted = quote.get("quoted_status") or {}
        meta["quote_state"] = quote.get("state") or ""
        meta["quoted_id"] = str(quoted.get("id") or quote.get("quoted_status_id") or "")

    application = status.get("application") or {}
    reply_to = "" if reblog is not None else str(status.get("in_reply_to_id") or "")
    return PostRecord(
        platform_post_id=str(status.get("id", "")),
        text=text,
        created_at=parse_datetime(status.get("created_at")),
        lang=str(source.get("language") or ""),
        kind=kind,
        reply_to=reply_to,
        mentions=mentions,
        hashtags=hashtags,
        urls=urls,
        client=str(application.get("name") or ""),
        meta=meta,
    )


@register
class MastodonConnector(Connector):
    name = "mastodon"
    title = "Mastodon (API pública de la instancia)"
    mode = "live"
    params: ClassVar[dict[str, str]] = {
        "handle": "Cuenta como usuario@instancia, p. ej. Mastodon@mastodon.social",
        "limit": "Máximo de publicaciones a recolectar (por defecto 50)",
        "graph_limit": "Máximo de seguidos y de seguidores a listar (por defecto 200)",
    }

    async def collect(
        self, *, handle: str, limit: int = 50, graph_limit: int = 200
    ) -> CollectionResult:
        user, instance = _split_account(require_text(handle, "handle"))
        limit = positive_int(limit, "limit", 50)
        graph_limit = positive_int(graph_limit, "graph_limit", 200)
        base = f"https://{instance}/api/v1"
        reference = f"https://{instance}/@{user}"
        warnings: list[str] = []
        raw: dict[str, Any] = {"statuses": [], "following": [], "followers": []}

        async with HttpSession(self.client) as http:
            try:
                found = await http.get_json(f"{base}/accounts/lookup", params={"acct": user})
            except RateLimited as exc:
                return empty_result(self.name, reference, [rate_limit_warning(exc)], raw)
            if found is None:
                raise ConnectorError(f"cuenta de Mastodon no encontrada en {instance}: {user}")
            if not isinstance(found, dict) or "id" not in found:
                raise ConnectorError(f"respuesta inesperada de lookup en {instance}")
            raw["account"] = found
            account_id = str(found["id"])

            posts = await self._statuses(
                http, base, account_id, instance, limit, raw["statuses"], warnings
            )
            following = await self._graph(
                http,
                base,
                account_id,
                "following",
                instance,
                graph_limit,
                found.get("following_count"),
                raw["following"],
                warnings,
            )
            followers = await self._graph(
                http,
                base,
                account_id,
                "followers",
                instance,
                graph_limit,
                found.get("followers_count"),
                raw["followers"],
                warnings,
            )

        account = self._account_record(found, instance, following, followers)
        subject_ref = f"account:mastodon:{account.handle.lower()}"
        entities = [
            EntityRecord(
                type="account",
                label=account.handle,
                props={"platform": "mastodon", "url": account.url},
                confidence=1.0,
                ref=subject_ref,
            )
        ]
        relations: list[RelationRecord] = []
        # Enlaces declarados en los campos del perfil (sitios propios verificados, por ejemplo).
        for field in found.get("fields") or []:
            for href in anchor_hrefs(parse_html(str(field.get("value") or ""))):
                url_ref = f"url:{href}"
                entities.append(
                    EntityRecord(
                        type="url",
                        label=href,
                        props={"source": "mastodon_profile_field"},
                        confidence=0.9,
                        ref=url_ref,
                    )
                )
                relations.append(
                    RelationRecord(
                        src_ref=subject_ref,
                        dst_ref=url_ref,
                        type="declares",
                        props={"field": str(field.get("name") or "")},
                        confidence=0.9,
                    )
                )

        return CollectionResult(
            connector=self.name,
            reference=reference,
            retrieved_at=utcnow(),
            profiles=[AccountProfile(account=account, posts=posts[:limit])],
            entities=entities,
            relations=relations,
            warnings=warnings,
            raw=raw,
        )

    def _account_record(
        self, data: dict[str, Any], instance: str, following: list[str], followers: list[str]
    ) -> AccountRecord:
        raw_acct = str(data.get("acct") or data.get("username") or "")
        handle = raw_acct if "@" in raw_acct else f"{raw_acct}@{instance}"
        return AccountRecord(
            platform="mastodon",
            handle=handle,
            platform_uid=f"{instance}:{data.get('id', '')}",
            display_name=html_to_text(str(data.get("display_name") or "")) or raw_acct,
            bio=html_to_text(str(data.get("note") or "")),
            url=str(data.get("url") or ""),
            created_at_platform=parse_datetime(data.get("created_at")),
            followers=data.get("followers_count"),
            following=data.get("following_count"),
            avatar_url=str(data.get("avatar") or ""),
            following_handles=following,
            follower_handles=followers,
            meta={
                "instance": instance,
                "statuses_count": data.get("statuses_count"),
                "bot": data.get("bot"),
                "locked": data.get("locked"),
                "last_status_at": data.get("last_status_at"),
            },
        )

    async def _statuses(
        self,
        http: HttpSession,
        base: str,
        account_id: str,
        instance: str,
        limit: int,
        pages: list[Any],
        warnings: list[str],
    ) -> list[PostRecord]:
        posts: list[PostRecord] = []
        url: str | None = f"{base}/accounts/{account_id}/statuses"
        params: dict[str, Any] | None = {"limit": min(STATUS_PAGE_MAX, limit)}
        try:
            while url and len(posts) < limit:
                data, url = await http.get_json_with_next(url, params=params)
                params = None  # la URL de Link ya lleva los parámetros de la consulta
                if not data:
                    break
                pages.append(data)
                posts.extend(_post(status, instance) for status in data)
        except RateLimited as exc:
            warnings.append(rate_limit_warning(exc))
        except ConnectorError as exc:
            warnings.append(f"publicaciones incompletas: {exc}")
        return posts

    async def _graph(
        self,
        http: HttpSession,
        base: str,
        account_id: str,
        kind: str,
        instance: str,
        limit: int,
        expected_count: int | None,
        pages: list[Any],
        warnings: list[str],
    ) -> list[str]:
        handles: list[str] = []
        url: str | None = f"{base}/accounts/{account_id}/{kind}"
        params: dict[str, Any] | None = {"limit": min(GRAPH_PAGE_MAX, limit)}
        try:
            while url and len(handles) < limit:
                data, url = await http.get_json_with_next(url, params=params)
                params = None
                if not data:
                    break
                pages.append(data)
                handles.extend(_full_acct(item.get("acct"), instance) for item in data)
        except RateLimited as exc:
            warnings.append(rate_limit_warning(exc))
        except ConnectorError as exc:
            warnings.append(f"no se pudo listar {kind}: {exc}")
        handles = dedupe(handles)[:limit]
        if not handles and expected_count:
            warnings.append(
                f"la instancia no expone la lista de {kind} (el perfil informa {expected_count})"
            )
        return handles
