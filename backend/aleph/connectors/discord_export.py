"""Importación de exportaciones JSON de DiscordChatExporter (un canal por archivo).

Genera un AccountProfile por autor, con sus mensajes. Estructura que se espera del exportador:
- Nivel superior: guild {id, name}, channel {id, name}, messages [...].
- Cada mensaje: id, type, timestamp (ISO 8601), timestampEdited, isPinned, content, author {id,
  name, nickname, discriminator, isBot, roles}, attachments, embeds, mentions [{id, name, ...}],
  y reference {messageId, channelId, guildId} cuando es una respuesta.

Sin verificar: no pude consultar el esquema del exportador desde este entorno; el JSON de ejemplo de
los tests reproduce la forma descrita arriba, no una exportación real. El parser acepta también las
variantes de la API de Discord (message_reference.message_id, author.username).

Por diseño: los adjuntos quedan en meta (son CDN de Discord, no enlaces del autor). Las URLs del
texto y de los embeds sí van en urls. No hay hashtags en Discord: se extraen del texto.
"""

import json
from pathlib import Path
from typing import Any, ClassVar

from aleph.connectors._util import (
    clean_handle,
    dedupe,
    extract_hashtags,
    extract_mentions,
    extract_urls,
    fix_mojibake_deep,
    norm_mention,
    parse_datetime,
    positive_int,
    read_text_file,
    sha256_hex,
    utcnow,
)
from aleph.connectors.base import Connector, ConnectorError, register
from aleph.core.schemas import AccountProfile, AccountRecord, CollectionResult, PostRecord


def _load_exports(path: Path) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
    """Lee un .json o todos los .json de una carpeta. Devuelve (nombre, exportación) y los archivos."""
    targets = sorted(path.glob("*.json")) if path.is_dir() else [path]
    if not targets:
        raise ConnectorError("no hay archivos .json de DiscordChatExporter en la carpeta indicada")
    exports: list[tuple[str, dict[str, Any]]] = []
    files: list[dict[str, Any]] = []
    for target in targets:
        raw_bytes = target.read_bytes()
        files.append(
            {"name": target.name, "sha256": sha256_hex(raw_bytes), "bytes": len(raw_bytes)}
        )
        try:
            document = json.loads(read_text_file(target))
        except ValueError as exc:
            raise ConnectorError(f"JSON ilegible en {target.name}: {exc}") from exc
        exports.append((target.name, fix_mojibake_deep(document)))
    return exports, files


def _reference_of(message: dict[str, Any]) -> str:
    ref = message.get("reference") or message.get("message_reference") or {}
    if not isinstance(ref, dict):
        return ""
    return str(ref.get("messageId") or ref.get("message_id") or "")


@register
class DiscordExportConnector(Connector):
    name = "discord_export"
    title = "Discord (exportación de DiscordChatExporter, JSON)"
    mode = "import"
    params: ClassVar[dict[str, str]] = {
        "path": "Archivo JSON de DiscordChatExporter (o carpeta con varios)",
        "data": "Alternativa a path: exportación ya leída (dict) o lista de exportaciones",
        "limit": "Máximo de mensajes a importar (opcional)",
    }

    async def collect(
        self,
        *,
        path: str | None = None,
        data: Any = None,
        limit: int | None = None,
    ) -> CollectionResult:
        if not path and data is None:
            raise ConnectorError("indicá 'path' (JSON de DiscordChatExporter) o 'data'")
        max_messages = positive_int(limit, "limit", 1) if limit is not None else None
        warnings: list[str] = []
        files: list[dict[str, Any]] = []

        if path:
            base = Path(path).expanduser()
            if not base.exists():
                raise ConnectorError(f"no existe la ruta indicada: {base.name}")
            exports, files = _load_exports(base)
            reference = str(base)
        else:
            documents = data if isinstance(data, list) else [data]
            exports = [("datos en memoria", fix_mojibake_deep(doc)) for doc in documents]
            reference = "datos en memoria"

        profiles: dict[str, AccountRecord] = {}
        posts: dict[str, list[PostRecord]] = {}
        raw_exports: list[Any] = []
        seen_ids: set[str] = set()  # dos exportaciones del mismo canal se solapan
        handled = 0
        for name, export in exports:
            raw_exports.append(export)
            if not isinstance(export, dict):
                warnings.append(f"{name}: formato no reconocido, se omite")
                continue
            guild = export.get("guild") or {}
            channel = export.get("channel") or {}
            channel_label = f"{guild.get('name') or ''}/#{channel.get('name') or ''}".strip("/#")
            for message in export.get("messages") or []:
                if max_messages is not None and handled >= max_messages:
                    break
                handled += 1
                if not isinstance(message, dict):
                    continue
                message_id = str(message.get("id") or "")
                if message_id and message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
                content = str(message.get("content") or "")
                attachments = [a for a in message.get("attachments") or [] if isinstance(a, dict)]
                embeds = [e for e in message.get("embeds") or [] if isinstance(e, dict)]
                if not content and not attachments and not embeds:
                    continue  # mensajes de sistema sin contenido
                author = message.get("author") or {}
                author_id = str(author.get("id") or "")
                username = clean_handle(author.get("name") or author.get("username") or author_id)
                if not author_id and not username:
                    continue
                key = author_id or username
                if key not in profiles:
                    profiles[key] = AccountRecord(
                        platform="discord",
                        handle=username,
                        platform_uid=author_id,
                        display_name=str(author.get("nickname") or author.get("name") or username),
                        meta={
                            "discriminator": author.get("discriminator"),
                            "is_bot": bool(author.get("isBot")),
                            "source": "discord_export",
                        },
                    )
                    posts[key] = []

                reply_to = _reference_of(message)
                if "mentions" in message:
                    mentions = dedupe(
                        norm_mention(m.get("name"))
                        for m in message.get("mentions") or []
                        if isinstance(m, dict)
                    )
                else:
                    mentions = extract_mentions(content)
                embed_urls = [str(e.get("url")) for e in embeds if e.get("url")]
                posts[key].append(
                    PostRecord(
                        platform_post_id=str(message.get("id") or ""),
                        text=content,
                        created_at=parse_datetime(message.get("timestamp")),
                        kind="reply" if reply_to or message.get("type") == "Reply" else "original",
                        reply_to=reply_to,
                        mentions=mentions,
                        hashtags=extract_hashtags(content),
                        urls=dedupe(extract_urls(content) + embed_urls),
                        client="",
                        meta={
                            "channel": channel_label,
                            "type": message.get("type"),
                            "edited_at": message.get("timestampEdited"),
                            "pinned": message.get("isPinned"),
                            "attachments": [
                                {
                                    "filename": a.get("fileName") or a.get("filename"),
                                    "url": a.get("url"),
                                }
                                for a in attachments
                            ],
                        },
                    )
                )
            if max_messages is not None and handled >= max_messages:
                break

        profiles_out = [AccountProfile(account=profiles[key], posts=posts[key]) for key in profiles]
        if not profiles_out:
            warnings.append("no se encontraron mensajes con contenido en la exportación")
        return CollectionResult(
            connector=self.name,
            reference=reference,
            retrieved_at=utcnow(),
            profiles=profiles_out,
            warnings=warnings,
            raw={"files": files, "exports": raw_exports},
        )
