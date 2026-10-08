"""Importación de la exportación oficial de Instagram (Descargar tu información, formato JSON).

Archivos que se buscan de forma recursiva (así sirven distintas versiones de la exportación):
- personal_information.json: perfil. Campos en string_map_data: Username, Name, Bio, Website, Email,
  Phone Number (bajo profile_user).
- posts_N.json: publicaciones. Cada una trae "media" (con uri y creation_timestamp), "title" (el pie)
  y creation_timestamp.
- post_comments_N.json: comentarios que hizo la cuenta, con string_map_data.Comment {value, timestamp}.
  El campo "title" se interpreta como el dueño de la publicación comentada.
- followers_N.json y following.json: listas de cuentas (string_list_data[].value).

Corrección de mojibake: los textos de Meta suelen venir como UTF-8 leído como latin-1 ("CafÃ©");
todo el JSON se corrige antes de normalizarlo.

Sin verificar: no hay documentación oficial del esquema accesible desde este entorno y no se probó
con una exportación real. Las claves anteriores son las habituales en la exportación JSON, no
confirmadas. El parser tolera variantes (timestamps en segundos o ISO, listas envueltas en un objeto,
pie en title o en media[0].title).
"""

import json
import re
from pathlib import Path
from typing import Any, ClassVar

from aleph.connectors._util import (
    clean_handle,
    dedupe,
    extract_hashtags,
    extract_mentions,
    extract_urls,
    fix_mojibake_deep,
    parse_datetime,
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

CATEGORIES = ("profile", "posts", "comments", "followers", "following")
PROFILE_FILE = re.compile(r"^personal_information\.json$", re.IGNORECASE)
POSTS_FILE = re.compile(r"^posts_\d+\.json$", re.IGNORECASE)
COMMENTS_FILE = re.compile(r"^post_comments(_\d+)?\.json$", re.IGNORECASE)
FOLLOWERS_FILE = re.compile(r"^followers(_\d+)?\.json$", re.IGNORECASE)
FOLLOWING_FILE = re.compile(r"^following\.json$", re.IGNORECASE)


def _category_of(name: str) -> str | None:
    if PROFILE_FILE.match(name):
        return "profile"
    if POSTS_FILE.match(name):
        return "posts"
    if COMMENTS_FILE.match(name):
        return "comments"
    if FOLLOWERS_FILE.match(name):
        return "followers"
    if FOLLOWING_FILE.match(name):
        return "following"
    return None


def _locate(path: Path) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {category: [] for category in CATEGORIES}
    candidates = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    for item in candidates:
        category = _category_of(item.name)
        if category:
            found[category].append(item)
    return found


def _load(text: str) -> Any:
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise ConnectorError(f"JSON ilegible en la exportación: {exc}") from exc
    return fix_mojibake_deep(value)


def _items(document: Any) -> list[dict[str, Any]]:
    """Lista de registros de un documento; acepta la lista directa o la primera lista del objeto."""
    if isinstance(document, dict):
        document = next((v for v in document.values() if isinstance(v, list)), [])
    return [item for item in document or [] if isinstance(item, dict)]


def _profile_fields(document: Any) -> dict[str, str]:
    items = _items(document)
    if not items:
        return {}
    first = items[0]
    fields = first.get("string_map_data") or {}

    def value(key: str) -> str:
        return str((fields.get(key) or {}).get("value") or "")

    return {
        "username": value("Username") or str(first.get("title") or ""),
        "name": value("Name"),
        "bio": value("Bio"),
        "website": value("Website"),
        "email": value("Email").strip().lower(),
        "phone": value("Phone Number"),
    }


def _post(item: dict[str, Any], index: int) -> PostRecord:
    media = [m for m in item.get("media") or [] if isinstance(m, dict)]
    first = media[0] if media else {}
    caption = str(item.get("title") or first.get("title") or "")
    timestamp = item.get("creation_timestamp") or first.get("creation_timestamp")
    post_id = str(first.get("uri") or f"post-{timestamp or 'sin-fecha'}-{index}")
    return PostRecord(
        platform_post_id=post_id,
        text=caption,
        created_at=parse_datetime(timestamp),
        kind="original",
        mentions=extract_mentions(caption),
        hashtags=extract_hashtags(caption),
        urls=extract_urls(caption),
        meta={"media_count": len(media), "media_uris": [str(m.get("uri", "")) for m in media]},
    )


def _comment(item: dict[str, Any], index: int, source: str) -> PostRecord:
    comment = (item.get("string_map_data") or {}).get("Comment") or {}
    text = str(comment.get("value") or "")
    timestamp = comment.get("timestamp")
    return PostRecord(
        platform_post_id=f"comment-{timestamp or 'sin-fecha'}-{index}",
        text=text,
        created_at=parse_datetime(timestamp),
        kind="reply",
        reply_to="",  # la exportación no trae el id de la publicación comentada
        mentions=extract_mentions(text),
        hashtags=extract_hashtags(text),
        urls=extract_urls(text),
        meta={"post_owner": str(item.get("title") or ""), "source_file": source},
    )


def _handles(document: Any) -> list[str]:
    handles: list[str] = []
    for item in _items(document):
        entries = item.get("string_list_data") or []
        value = (
            str(entries[0].get("value") or "") if entries and isinstance(entries[0], dict) else ""
        )
        handles.append(clean_handle(value or item.get("title")).lower())
    return dedupe(h for h in handles if h)


@register
class InstagramExportConnector(Connector):
    name = "instagram_export"
    title = "Instagram (exportación oficial en JSON)"
    mode = "import"
    params: ClassVar[dict[str, str]] = {
        "path": "Carpeta descomprimida de la exportación de Instagram (o un .json suelto)",
        "data": "Alternativa a path: {'profile', 'posts', 'comments', 'followers', 'following'} ya leídos",
        "handle": "Usuario de la cuenta, si el perfil no lo trae (opcional)",
        "limit": "Máximo de publicaciones y comentarios a importar (opcional)",
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
            raise ConnectorError("indicá 'path' (carpeta de la exportación) o 'data'")
        max_items = positive_int(limit, "limit", 1) if limit is not None else None
        warnings: list[str] = []
        files: list[dict[str, Any]] = []
        documents: dict[str, list[Any]] = {category: [] for category in CATEGORIES}

        if path:
            base = Path(path).expanduser()
            if not base.exists():
                raise ConnectorError(f"no existe la ruta indicada: {base.name}")
            reference = str(base)
            for category, found in _locate(base).items():
                for item in found:
                    raw_bytes = item.read_bytes()
                    files.append(
                        {
                            "name": item.name,
                            "sha256": sha256_hex(raw_bytes),
                            "bytes": len(raw_bytes),
                        }
                    )
                    documents[category].append(_load(read_text_file(item)))
        else:
            reference = "datos en memoria"
            for category in CATEGORIES:
                value = (data or {}).get(category)
                if value is None:
                    continue
                if isinstance(value, str):
                    files.append(
                        {
                            "name": f"{category}.json",
                            "sha256": sha256_hex(value.encode("utf-8")),
                            "bytes": len(value),
                        }
                    )
                    value = _load(value)
                documents[category].append(fix_mojibake_deep(value))

        fields = _profile_fields(documents["profile"][0]) if documents["profile"] else {}
        username = clean_handle(fields.get("username") or handle)
        if not username:
            raise ConnectorError(
                "no encontré el usuario en personal_information.json: indicá 'handle'"
            )
        if not documents["profile"]:
            warnings.append(
                "no hay personal_information.json: el perfil se arma sólo con el handle"
            )

        raw_posts = [item for doc in documents["posts"] for item in _items(doc)]
        raw_comments = [item for doc in documents["comments"] for item in _items(doc)]
        posts = [_post(item, index) for index, item in enumerate(raw_posts)]
        comments = [
            _comment(item, index, "post_comments") for index, item in enumerate(raw_comments)
        ]
        everything = posts + comments
        if max_items is not None:
            everything = everything[:max_items]
        unique: dict[str, PostRecord] = {}
        for post in everything:
            unique.setdefault(post.platform_post_id, post)
        if not raw_posts:
            warnings.append("la exportación no trae publicaciones (posts_N.json)")

        followers = dedupe(h for doc in documents["followers"] for h in _handles(doc))
        following = dedupe(h for doc in documents["following"] for h in _handles(doc))

        account = AccountRecord(
            platform="instagram",
            handle=username,
            display_name=fields.get("name", ""),
            bio=fields.get("bio", ""),
            url=f"https://www.instagram.com/{username}/",
            followers=len(followers) if documents["followers"] else None,
            following=len(following) if documents["following"] else None,
            following_handles=following,
            follower_handles=followers,
            meta={"source": "instagram_export"},
        )

        subject_ref = f"account:instagram:{username.lower()}"
        entities = [
            EntityRecord(
                type="account",
                label=username,
                props={"platform": "instagram", "url": account.url},
                confidence=1.0,
                ref=subject_ref,
            )
        ]
        relations: list[RelationRecord] = []
        for key, entity_type, field in (
            ("email", "email", "email"),
            ("phone", "phone", "phone"),
            ("website", "url", "website"),
        ):
            value = fields.get(key, "").strip()
            if not value or (entity_type == "email" and "@" not in value):
                continue
            ref = f"{entity_type}:{value.lower()}"
            entities.append(
                EntityRecord(
                    type=entity_type,
                    label=value,
                    props={
                        "source": "instagram_export:personal_information.json",
                        "sensitive": True,
                    },
                    confidence=1.0 if field != "website" else 0.8,
                    ref=ref,
                )
            )
            relations.append(
                RelationRecord(
                    src_ref=subject_ref,
                    dst_ref=ref,
                    type="declares",
                    props={"field": field},
                    confidence=1.0 if field != "website" else 0.8,
                )
            )

        return CollectionResult(
            connector=self.name,
            reference=reference,
            retrieved_at=utcnow(),
            profiles=[AccountProfile(account=account, posts=list(unique.values()))],
            entities=entities,
            relations=relations,
            warnings=warnings,
            raw={
                "files": files,
                "profile": documents["profile"],
                "posts": raw_posts,
                "comments": raw_comments,
                "followers": documents["followers"],
                "following": documents["following"],
            },
        )
