"""Internet Archive: capturas de una URL (API CDX), primera y última captura, cambios de contenido
entre capturas consecutivas (por digest) y captura más cercana a una fecha (API de disponibilidad).

Documentación oficial en la que se basa:
- API CDX: https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md
  «limit=N» devuelve las primeras N filas y un límite negativo devuelve las últimas N; con
  output=json la primera fila es la cabecera con los nombres de campo.
- API de disponibilidad: https://archive.org/help/wayback_api.php (archived_snapshots.closest).

Solo lectura. Las URL de captura se construyen con el formato público web.archive.org/web/<timestamp>/<url>
y no se descargan. Las consultas de capturas usan el tope «max_captures»; si se alcanza, la última captura
se pide aparte con un límite negativo y los cambios solo se calculan sobre las capturas leídas.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from ._http import get_json, open_client, request
from .errors import InvalidInputError, UpstreamError

CDX_URL = "https://web.archive.org/cdx/search/cdx"
AVAILABILITY_URL = "https://archive.org/wayback/available"
CDX_FIELDS = "timestamp,original,mimetype,statuscode,digest,length"
DEFAULT_MAX_CAPTURES = 5000
_TS_RE = re.compile(r"[0-9]{1,14}")
_TS_FULL_RE = re.compile(r"[0-9]{14}")


class Capture(BaseModel):
    timestamp: datetime  # UTC
    original: str
    mimetype: str = ""
    statuscode: str = ""
    digest: str = ""  # digest de contenido tal como lo devuelve CDX
    length: int | None = None
    archive_url: str


class ContentChange(BaseModel):
    timestamp: datetime
    previous_digest: str
    digest: str
    archive_url: str


class ClosestSnapshot(BaseModel):
    available: bool
    url: str
    timestamp: datetime | None = None
    status: str = ""


class WaybackReport(BaseModel):
    url: str
    captures_fetched: int
    truncated: bool
    first_capture: Capture | None = None
    last_capture: Capture | None = None
    distinct_digests: int = 0
    changes: list[ContentChange] = Field(default_factory=list)
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


def _validate_target(url: str) -> str:
    value = (url or "").strip()
    if not value or len(value) > 2048 or any(ch.isspace() for ch in value):
        raise InvalidInputError("URL o dominio vacío, demasiado largo o con espacios")
    return value


def _parse_ts(text: str) -> datetime | None:
    if not _TS_FULL_RE.fullmatch(text or ""):
        return None
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _validate_ts(value: str | None) -> str | None:
    if value is None:
        return None
    if not _TS_RE.fullmatch(value):
        raise InvalidInputError("timestamp inválido: se esperan de 1 a 14 dígitos (AAAAMMDDhhmmss)")
    return value


async def _cdx(client: httpx.AsyncClient, url: str, *, limit: int, from_ts: str | None,
               to_ts: str | None) -> tuple[list, list[dict[str, str]]]:
    params: dict[str, str] = {"url": url, "output": "json", "fl": CDX_FIELDS, "limit": str(limit)}
    if from_ts:
        params["from"] = from_ts
    if to_ts:
        params["to"] = to_ts
    resp = await request(client, "GET", CDX_URL, params=params)
    if resp.status_code != 200:
        raise UpstreamError(f"la API CDX respondió HTTP {resp.status_code}",
                            status_code=resp.status_code, url=CDX_URL)
    text = resp.text.strip()
    if not text:  # sin capturas: la API devuelve cuerpo vacío
        return [], []
    try:
        rows = json.loads(text)
    except ValueError as exc:
        raise UpstreamError("la API CDX no devolvió JSON válido", url=CDX_URL) from exc
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
        raise UpstreamError("formato inesperado en la respuesta CDX", url=CDX_URL)
    header = [str(name) for name in rows[0]]
    records = [dict(zip(header, row)) for row in rows[1:] if isinstance(row, list) and len(row) == len(header)]
    return rows, records


def _to_capture(record: dict[str, str]) -> Capture | None:
    stamp = record.get("timestamp", "")
    moment = _parse_ts(stamp)
    original = record.get("original", "")
    if moment is None or not original:
        return None
    length_text = record.get("length", "")
    return Capture(
        timestamp=moment,
        original=original,
        mimetype=record.get("mimetype", ""),
        statuscode=record.get("statuscode", ""),
        digest=record.get("digest", "") if record.get("digest", "") != "-" else "",
        length=int(length_text) if length_text.isdigit() else None,
        archive_url=f"https://web.archive.org/web/{stamp}/{original}",
    )


async def list_captures(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    limit: int = 1000,
    from_ts: str | None = None,
    to_ts: str | None = None,
    newest: bool = False,
) -> list[Capture]:
    """Capturas de una URL en orden cronológico. Con newest=True devuelve las últimas «limit»."""
    target = _validate_target(url)
    if limit < 1:
        raise InvalidInputError("limit debe ser mayor que cero")
    async with open_client(client) as http:
        _, records = await _cdx(
            http, target, limit=-limit if newest else limit,
            from_ts=_validate_ts(from_ts), to_ts=_validate_ts(to_ts),
        )
    return [cap for cap in (_to_capture(rec) for rec in records) if cap is not None]


async def closest_snapshot(
    url: str,
    *,
    timestamp: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> ClosestSnapshot | None:
    """Captura más cercana a «timestamp» (o la más reciente si se omite). None si no hay ninguna."""
    target = _validate_target(url)
    params: dict[str, str] = {"url": target}
    if timestamp is not None:
        params["timestamp"] = _validate_ts(timestamp) or ""
    async with open_client(client) as http:
        data = await get_json(http, AVAILABILITY_URL, params=params)
    snapshots = data.get("archived_snapshots") if isinstance(data, dict) else None
    closest = snapshots.get("closest") if isinstance(snapshots, dict) else None
    if not isinstance(closest, dict) or not closest:
        return None
    return ClosestSnapshot(
        available=bool(closest.get("available")),
        url=str(closest.get("url", "")),
        timestamp=_parse_ts(str(closest.get("timestamp", ""))),
        status=str(closest.get("status", "")),
    )


async def analyze_url(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_captures: int = DEFAULT_MAX_CAPTURES,
    from_ts: str | None = None,
    to_ts: str | None = None,
) -> WaybackReport:
    """Historial de capturas de una URL: primera y última captura, y cambios de digest."""
    target = _validate_target(url)
    if max_captures < 1:
        raise InvalidInputError("max_captures debe ser mayor que cero")
    start, end = _validate_ts(from_ts), _validate_ts(to_ts)
    warnings: list[str] = []
    raw: dict[str, Any] = {}
    async with open_client(client) as http:
        rows, records = await _cdx(http, target, limit=max_captures, from_ts=start, to_ts=end)
        raw["cdx"] = rows
        captures = [cap for cap in (_to_capture(rec) for rec in records) if cap is not None]
        if len(records) != len(captures):
            warnings.append(f"{len(records) - len(captures)} filas CDX mal formadas se descartaron")
        truncated = len(records) >= max_captures
        last = captures[-1] if captures else None
        if truncated:
            newest_rows, newest_records = await _cdx(http, target, limit=-1, from_ts=start, to_ts=end)
            raw["cdx_newest"] = newest_rows
            newest = [cap for cap in (_to_capture(rec) for rec in newest_records) if cap is not None]
            if newest:
                last = newest[-1]
            warnings.append(
                f"se leyeron las primeras {max_captures} capturas: los cambios de contenido se calculan "
                "solo sobre ellas; la última captura se obtuvo con una consulta aparte"
            )

    changes: list[ContentChange] = []
    digests: set[str] = set()
    previous: Capture | None = None
    for cap in captures:
        if cap.digest:
            digests.add(cap.digest)
        if previous is not None and previous.digest and cap.digest and previous.digest != cap.digest:
            changes.append(ContentChange(
                timestamp=cap.timestamp, previous_digest=previous.digest, digest=cap.digest,
                archive_url=cap.archive_url,
            ))
        previous = cap

    first = captures[0] if captures else None
    entities = [EntityRecord(
        type="url", label=target, ref="url", confidence=1.0,
        props={
            "primera_captura": first.timestamp.isoformat() if first else "",
            "ultima_captura": last.timestamp.isoformat() if last else "",
            "capturas_leidas": len(captures), "cambios_de_contenido": len(changes),
            "truncado": truncated, "fuente": "Internet Archive CDX",
        },
    )]
    return WaybackReport(
        url=target, captures_fetched=len(captures), truncated=truncated,
        first_capture=first, last_capture=last, distinct_digests=len(digests),
        changes=changes, entities=entities, warnings=warnings, raw=raw,
    )
