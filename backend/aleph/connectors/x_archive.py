"""Importación del archivo de datos que X entrega al titular de la cuenta (carpeta data/).

Archivos que se leen, todos con el prefijo JavaScript `window.YTD.<nombre>.partN = [...]`:
- tweets.js (o tweet*.js): publicaciones, como [{"tweet": {...}}, ...]. Campos usados: id_str,
  created_at ("Wed Oct 10 20:19:24 +0000 2018"), full_text, lang, source (HTML), entities
  (hashtags[].text, user_mentions[].screen_name, urls[].expanded_url) e in_reply_to_status_id_str.
- account.js: [{"account": {"username", "accountId", "createdAt", "accountDisplayName", "email"...}}].
- profile.js: [{"profile": {"description": {"bio", "website", "location"}, "avatarMediaUrl"...}}].

Sin verificar: no hay documentación oficial del esquema accesible desde este entorno, y no se probó
con un archivo real de X. El formato se basa en el conocimiento de la exportación; el parser tolera
claves ausentes. Si falta el campo entities, el conector extrae menciones, hashtags y URLs del texto.

Por diseño, "kind" usa sólo señales explícitas: "RT @" al inicio es repost e in_reply_to_status_id
es reply. Las citas no se infieren: los enlaces a otras publicaciones quedan en meta.status_links.
"""

import re
from pathlib import Path
from typing import Any, ClassVar

from aleph.connectors._util import (
    clean_handle,
    dedupe,
    extract_hashtags,
    extract_mentions,
    extract_urls,
    html_to_text,
    norm_hashtag,
    norm_mention,
    parse_datetime,
    parse_ytd_js,
    positive_int,
    read_text_file,
    sha256_hex,
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

STATUS_URL_RE = re.compile(r"^https?://(?:www\.)?(?:twitter|x)\.com/[^/]+/status/\d+")


def _unwrap(items: Any, key: str) -> list[dict[str, Any]]:
    """[{"tweet": {...}}, ...] -> [{...}, ...]. Acepta también objetos ya desenvueltos."""
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            inner = item.get(key)
            out.append(inner if isinstance(inner, dict) else item)
    return out


def _tweet_post(tweet: dict[str, Any]) -> PostRecord:
    text = str(tweet.get("full_text") or tweet.get("text") or "")
    reply_to = str(tweet.get("in_reply_to_status_id_str") or "")
    if text.startswith("RT @"):
        kind = "repost"
    elif reply_to:
        kind = "reply"
    else:
        kind = "original"

    entities = tweet.get("entities")
    if isinstance(entities, dict):
        mentions = dedupe(
            norm_mention(m.get("screen_name")) for m in entities.get("user_mentions") or []
        )
        hashtags = dedupe(norm_hashtag(h.get("text")) for h in entities.get("hashtags") or [])
        urls = dedupe(
            str(u.get("expanded_url") or u.get("url") or "") for u in entities.get("urls") or []
        )
    else:
        mentions = extract_mentions(text)
        hashtags = extract_hashtags(text)
        urls = extract_urls(text)
    urls = [u for u in urls if u]

    lang = str(tweet.get("lang") or "")
    return PostRecord(
        platform_post_id=str(tweet.get("id_str") or tweet.get("id") or ""),
        text=text,
        created_at=parse_datetime(tweet.get("created_at")),
        lang="" if lang == "und" else lang,
        kind=kind,
        reply_to=reply_to,
        mentions=mentions,
        hashtags=hashtags,
        urls=urls,
        client=html_to_text(str(tweet.get("source") or "")),
        meta={
            "favorite_count": tweet.get("favorite_count"),
            "retweet_count": tweet.get("retweet_count"),
            "in_reply_to_screen_name": tweet.get("in_reply_to_screen_name"),
            "possibly_sensitive": tweet.get("possibly_sensitive"),
            "status_links": [u for u in urls if STATUS_URL_RE.match(u)],
        },
    )


def _locate_files(path: Path) -> dict[str, list[Path]]:
    """Archivos del archivo de X en una carpeta (raíz o data/), o junto a un .js puntual."""
    folders = [path.parent] if path.is_file() else [path, path / "data"]
    found: dict[str, list[Path]] = {"tweets": [], "account": [], "profile": []}
    for folder in folders:
        if not folder.is_dir():
            continue
        for item in sorted(folder.iterdir()):
            name = item.name.lower()
            if not item.is_file() or not name.endswith(".js"):
                continue
            if name.startswith("tweet"):
                found["tweets"].append(item)
            elif name == "account.js":
                found["account"].append(item)
            elif name == "profile.js":
                found["profile"].append(item)
    if path.is_file() and path.name.lower().startswith("tweet"):
        found["tweets"] = [path]  # un tweets.js puntual: sólo sus publicaciones
    return found


@register
class XArchiveConnector(Connector):
    name = "x_archive"
    title = "Archivo de datos de X (importación)"
    mode = "import"
    params: ClassVar[dict[str, str]] = {
        "path": "Carpeta descomprimida del archivo de X (con data/), o la ruta a tweets.js",
        "data": "Alternativa a path: {'tweets', 'account', 'profile'} con texto JS o listas ya leídas",
        "handle": "Usuario de la cuenta, si no hay account.js (opcional)",
        "limit": "Máximo de publicaciones a importar (opcional)",
    }

    async def collect(
        self,
        *,
        path: str | None = None,
        data: dict[str, Any] | None = None,
        handle: str | None = None,
        limit: int | None = None,
    ) -> CollectionResult:
        if not path and not data:
            raise ConnectorError("indicá 'path' (carpeta del archivo de X) o 'data'")
        max_items = positive_int(limit, "limit", 1) if limit is not None else None
        warnings: list[str] = []
        files: list[dict[str, Any]] = []
        sources: dict[str, list[Any]] = {"tweets": [], "account": [], "profile": []}

        if path:
            base = Path(path).expanduser()
            if not base.exists():
                raise ConnectorError(f"no existe la ruta indicada: {base.name}")
            reference = str(base)
            for kind, found in _locate_files(base).items():
                for item in found:
                    raw_bytes = item.read_bytes()
                    files.append(
                        {
                            "name": item.name,
                            "sha256": sha256_hex(raw_bytes),
                            "bytes": len(raw_bytes),
                        }
                    )
                    parsed = parse_ytd_js(read_text_file(item))
                    if isinstance(parsed, list):
                        sources[kind].extend(parsed)
        else:
            reference = "datos en memoria"
            for kind in ("tweets", "account", "profile"):
                value = (data or {}).get(kind)
                if value is None:
                    continue
                if isinstance(value, str):
                    files.append(
                        {
                            "name": f"{kind}.js",
                            "sha256": sha256_hex(value.encode("utf-8")),
                            "bytes": len(value),
                        }
                    )
                    value = parse_ytd_js(value)
                if isinstance(value, list):
                    sources[kind].extend(value)

        if not sources["tweets"]:
            warnings.append("no se encontraron publicaciones (tweets.js) en la fuente")

        account_obj = (_unwrap(sources["account"], "account") or [{}])[0]
        profile_obj = (_unwrap(sources["profile"], "profile") or [{}])[0]
        username = clean_handle(account_obj.get("username") or handle)
        if not username:
            raise ConnectorError(
                "no encontré account.js: indicá 'handle' con el usuario de la cuenta"
            )
        if not account_obj:
            warnings.append("no hay account.js: el perfil se arma sólo con el handle indicado")

        description = profile_obj.get("description") or {}
        account = AccountRecord(
            platform="x",
            handle=username,
            platform_uid=str(account_obj.get("accountId") or ""),
            display_name=str(account_obj.get("accountDisplayName") or ""),
            bio=str(description.get("bio") or ""),
            url=f"https://x.com/{username}",
            created_at_platform=parse_datetime(account_obj.get("createdAt")),
            avatar_url=str(profile_obj.get("avatarMediaUrl") or ""),
            meta={
                "created_via": account_obj.get("createdVia"),
                "location": description.get("location"),
                "header_url": profile_obj.get("headerMediaUrl"),
                "source": "x_archive",
            },
        )

        raw_tweets = _unwrap(sources["tweets"], "tweet")
        if max_items is not None:
            raw_tweets = raw_tweets[:max_items]
        posts: list[PostRecord] = []
        seen: set[str] = set()
        for tweet in raw_tweets:
            post = _tweet_post(tweet)
            if post.platform_post_id and post.platform_post_id not in seen:
                seen.add(post.platform_post_id)
                posts.append(post)

        subject_ref = f"account:x:{username.lower()}"
        entities = [
            EntityRecord(
                type="account",
                label=username,
                props={"platform": "x", "url": account.url},
                confidence=1.0,
                ref=subject_ref,
            )
        ]
        relations: list[RelationRecord] = []
        email = str(account_obj.get("email") or "").strip().lower()
        if "@" in email:
            entities.append(
                EntityRecord(
                    type="email",
                    label=email,
                    props={"source": "x_archive:account.js", "sensitive": True},
                    confidence=1.0,
                    ref=f"email:{email}",
                )
            )
            relations.append(
                RelationRecord(
                    src_ref=subject_ref,
                    dst_ref=f"email:{email}",
                    type="declares",
                    props={"field": "email"},
                    confidence=1.0,
                )
            )
        website = str(description.get("website") or "").strip()
        if website:
            entities.append(
                EntityRecord(
                    type="url",
                    label=website,
                    props={"source": "x_archive:profile.js"},
                    confidence=0.8,
                    ref=f"url:{website}",
                )
            )
            relations.append(
                RelationRecord(
                    src_ref=subject_ref,
                    dst_ref=f"url:{website}",
                    type="declares",
                    props={"field": "website"},
                    confidence=0.8,
                )
            )

        return CollectionResult(
            connector=self.name,
            reference=reference,
            retrieved_at=utcnow(),
            profiles=[AccountProfile(account=account, posts=posts)],
            entities=entities,
            relations=relations,
            warnings=warnings,
            raw={
                "files": files,
                "account": account_obj,
                "profile": profile_obj,
                "tweets": raw_tweets,
            },
        )
