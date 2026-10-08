"""Importación genérica de CSV o JSONL con columnas mapeables a publicaciones y cuentas.

Campos canónicos (nombre de columna por defecto, sin distinguir mayúsculas; espacios -> "_"):
  platform, handle (obligatorio), text, created_at, post_id, kind, reply_to, lang, client,
  mentions, hashtags, urls, display_name, bio, url, platform_uid, followers, following.
- Listas (mentions, hashtags, urls): separadas por ";", "," o "|" (urls también por espacios), o
  como lista JSON. Si la columna no existe o está vacía, se extraen del texto.
- Fechas: ISO 8601, epoch o formato de X; sin zona horaria se asumen UTC. Las fechas inválidas
  quedan en None y se cuentan en warnings.
- Si no hay post_id se genera un hash estable (plataforma, handle, fecha y texto), para que
  reimportar el mismo archivo no duplique publicaciones.
- kind: original, reply, repost o quote; si no viene, reply cuando hay reply_to y original en otro caso.
- El parámetro mapping cambia los nombres de columna, p. ej. {"handle": "usuario", "text": "contenido"}.

Sin verificar: no aplica (formato propio, no hay API externa).
"""

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, ClassVar

from aleph.connectors._util import (
    KINDS,
    clean_handle,
    dedupe,
    extract_hashtags,
    extract_mentions,
    extract_urls,
    norm_hashtag,
    norm_mention,
    parse_datetime,
    positive_int,
    read_text_file,
    sha256_hex,
    utcnow,
)
from aleph.connectors.base import Connector, ConnectorError, register
from aleph.core.schemas import AccountProfile, AccountRecord, CollectionResult, PostRecord

LIST_SPLIT = {"mentions": r"[;,|]", "hashtags": r"[;,|]", "urls": r"[;|\s]+"}


def _norm_header(name: Any) -> str:
    return str(name or "").strip().lower().replace(" ", "_")


def _split(value: Any, pattern: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None and str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if str(v).strip()]
        except ValueError:
            pass
    return [part.strip() for part in re.split(pattern, text) if part.strip()]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _rows_from_csv(text: str) -> list[dict[str, Any]]:
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    return [dict(row) for row in csv.DictReader(io.StringIO(text), delimiter=delimiter)]


def _rows_from_jsonl(text: str) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    bad = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(value, dict):
            rows.append(value)
        else:
            bad += 1
    return rows, bad


def _row_value(row: dict[str, Any], mapping: dict[str, str], field: str) -> str:
    value = row.get(mapping.get(field, field))
    return "" if value is None else str(value).strip()


@register
class GenericCsvConnector(Connector):
    name = "generic_csv"
    title = "CSV / JSONL genérico (importación)"
    mode = "import"
    params: ClassVar[dict[str, str]] = {
        "path": "Archivo .csv, .txt, .jsonl o .ndjson",
        "data": "Alternativa a path: texto CSV/JSONL, o lista de diccionarios",
        "format": "csv, jsonl o auto (por defecto auto)",
        "mapping": 'Columnas por campo canónico, p. ej. {"handle": "usuario", "text": "contenido"}',
        "platform": "Plataforma por defecto si la fila no trae 'platform' (por defecto 'generic')",
        "limit": "Máximo de filas a importar (opcional)",
    }

    async def collect(
        self,
        *,
        path: str | None = None,
        data: Any = None,
        format: str = "auto",
        mapping: dict[str, str] | str | None = None,
        platform: str = "generic",
        limit: int | None = None,
    ) -> CollectionResult:
        if not path and data is None:
            raise ConnectorError("indicá 'path' (archivo CSV o JSONL) o 'data'")
        max_rows = positive_int(limit, "limit", 1) if limit is not None else None
        if isinstance(mapping, str):
            try:
                mapping = json.loads(mapping)
            except ValueError as exc:
                raise ConnectorError("'mapping' debe ser un objeto JSON") from exc
        mapping_norm = {str(k): _norm_header(v) for k, v in (mapping or {}).items()}

        warnings: list[str] = []
        files: list[dict[str, Any]] = []
        bad_lines = 0
        if path:
            source = Path(path).expanduser()
            if not source.exists():
                raise ConnectorError(f"no existe la ruta indicada: {source.name}")
            text = read_text_file(source)
            raw_bytes = source.read_bytes()
            files.append(
                {"name": source.name, "sha256": sha256_hex(raw_bytes), "bytes": len(raw_bytes)}
            )
            reference = str(source)
            fmt = format
            if fmt == "auto":
                fmt = "jsonl" if source.suffix.lower() in (".jsonl", ".ndjson") else "csv"
            if fmt == "jsonl":
                rows, bad_lines = _rows_from_jsonl(text)
            else:
                rows = _rows_from_csv(text)
        else:
            reference = "datos en memoria"
            if isinstance(data, str):
                fmt = (
                    format
                    if format != "auto"
                    else ("jsonl" if data.lstrip().startswith("{") else "csv")
                )
                if fmt == "jsonl":
                    rows, bad_lines = _rows_from_jsonl(data)
                else:
                    rows = _rows_from_csv(data)
            elif isinstance(data, list):
                rows = [row for row in data if isinstance(row, dict)]
            else:
                raise ConnectorError("'data' debe ser texto CSV/JSONL o una lista de diccionarios")

        if bad_lines:
            warnings.append(f"{bad_lines} líneas JSONL inválidas omitidas")

        accounts: dict[tuple[str, str], dict[str, Any]] = {}
        skipped = invalid_dates = invalid_kinds = processed = 0
        for raw_row in rows:
            if max_rows is not None and processed >= max_rows:
                break
            processed += 1
            row = {_norm_header(k): v for k, v in raw_row.items() if k is not None}

            handle = clean_handle(_row_value(row, mapping_norm, "handle"))
            if not handle:
                skipped += 1
                continue
            plat = (_row_value(row, mapping_norm, "platform") or platform or "generic").lower()
            text = _row_value(row, mapping_norm, "text")

            raw_date = _row_value(row, mapping_norm, "created_at")
            created = parse_datetime(raw_date) if raw_date else None
            if raw_date and created is None:
                invalid_dates += 1

            reply_to = _row_value(row, mapping_norm, "reply_to")
            raw_kind = _row_value(row, mapping_norm, "kind").lower()
            if raw_kind in KINDS:
                kind = raw_kind
            else:
                if raw_kind:
                    invalid_kinds += 1
                kind = "reply" if reply_to else "original"

            post_id = _row_value(row, mapping_norm, "post_id") or (
                "row-" + sha256_hex(f"{plat}|{handle}|{raw_date}|{text}".encode())[:16]
            )
            mentions = _split(
                row.get(mapping_norm.get("mentions", "mentions")), LIST_SPLIT["mentions"]
            )
            hashtags = _split(row.get(mapping_norm.get("hashtags", "hashtags")), r"[;,|]")
            urls = _split(row.get(mapping_norm.get("urls", "urls")), LIST_SPLIT["urls"])
            post = PostRecord(
                platform_post_id=post_id,
                text=text,
                created_at=created,
                lang=_row_value(row, mapping_norm, "lang"),
                kind=kind,
                reply_to=reply_to,
                mentions=dedupe(norm_mention(m) for m in mentions) or extract_mentions(text),
                hashtags=dedupe(norm_hashtag(h) for h in hashtags) or extract_hashtags(text),
                urls=dedupe(urls) or extract_urls(text),
                client=_row_value(row, mapping_norm, "client"),
            )

            entry = accounts.setdefault(
                (plat, handle.lower()), {"handle": handle, "fields": {}, "posts": []}
            )
            for field in ("display_name", "bio", "url", "platform_uid"):
                value = _row_value(row, mapping_norm, field)
                if value and not entry["fields"].get(field):
                    entry["fields"][field] = value
            for field in ("followers", "following"):
                number = _int_or_none(_row_value(row, mapping_norm, field))
                if number is not None and entry["fields"].get(field) is None:
                    entry["fields"][field] = number
            entry["posts"].append(post)

        if skipped:
            warnings.append(f"{skipped} filas sin handle omitidas")
        if invalid_dates:
            warnings.append(f"{invalid_dates} fechas no interpretables (quedaron vacías)")
        if invalid_kinds:
            warnings.append(f"{invalid_kinds} valores de kind no válidos (se infirió el tipo)")

        profiles: list[AccountProfile] = []
        for (plat, _), entry in accounts.items():
            fields = entry["fields"]
            profiles.append(
                AccountProfile(
                    account=AccountRecord(
                        platform=plat,
                        handle=entry["handle"],
                        platform_uid=str(fields.get("platform_uid", "")),
                        display_name=str(fields.get("display_name", "")),
                        bio=str(fields.get("bio", "")),
                        url=str(fields.get("url", "")),
                        followers=fields.get("followers"),
                        following=fields.get("following"),
                        meta={"source": "generic_csv"},
                    ),
                    posts=entry["posts"],
                )
            )

        if not profiles:
            warnings.append("no se importó ninguna cuenta: revisá el mapeo de columnas")
        return CollectionResult(
            connector=self.name,
            reference=reference,
            retrieved_at=utcnow(),
            profiles=profiles,
            warnings=warnings,
            raw={"files": files, "rows": rows[:processed]},
        )
