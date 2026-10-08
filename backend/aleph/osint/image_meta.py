"""Metadatos de imagen: EXIF (cámara, software, fecha, GPS), hashes y enlaces de búsqueda inversa.

Cálculo local con Pillow e imagehash. No consulta ninguna API externa. Los enlaces de búsqueda
inversa solo se construyen como URL; no se abren ni se consultan.

Referencias:
- Pillow, EXIF (Image.Exif, get_ifd): https://pillow.readthedocs.io/en/stable/reference/Image.html
- imagehash (pHash): https://github.com/JohannesBuchner/imagehash

Advertencia: el EXIF lo escribe la cámara o el software de edición y se puede falsificar o borrar.
La ubicación GPS es una hipótesis que hay que corroborar con otras fuentes.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import imagehash
from PIL import ExifTags, Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from .errors import InvalidInputError

MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_URL_LEN = 2048
GPS_CONFIDENCE = 0.7  # EXIF GPS: plausible pero falsificable

_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_TAG_SOFTWARE = 0x0131
_TAG_DATETIME = 0x0132
_TAG_MAKER_NOTE = 0x927C
_IFD_EXIF = 0x8769
_IFD_GPS = 0x8825
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_OFFSET_TIME_ORIGINAL = 0x9011
_GPS_LAT_REF = 1
_GPS_LAT = 2
_GPS_LON_REF = 3
_GPS_LON = 4
_GPS_ALT_REF = 5
_GPS_ALT = 6
_GPS_TIME = 7
_GPS_DATE = 29


class GpsPoint(BaseModel):
    lat: float
    lon: float
    altitude_m: float | None = None
    timestamp_utc: str = ""  # GPSDateStamp + GPSTimeStamp, que el estándar expresa en UTC


class ImageMetaResult(BaseModel):
    filename: str = ""
    sha256: str
    size_bytes: int
    format: str = ""
    width: int = 0
    height: int = 0
    mode: str = ""
    phash: str = ""  # pHash de imagehash, 64 bits en hexadecimal
    camera_make: str = ""
    camera_model: str = ""
    software: str = ""
    taken_at: str = ""  # fecha de captura tal como figura en el EXIF
    taken_at_iso: str = ""  # ISO 8601; incluye zona solo si el EXIF la indica
    gps: GpsPoint | None = None
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)  # EXIF saneado, por nombre de etiqueta


class ReverseSearchLink(BaseModel):
    engine: str
    title: str
    url: str


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).replace("\x00", "").strip() if value is not None else ""


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def _sanitize(value: Any, depth: int = 0) -> Any:
    """Convierte valores EXIF (racionales, bytes, tuplas) a tipos JSON sin volcar blobs."""
    if depth > 4:
        return "<anidado>"
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        return value.replace("\x00", "")[:1000]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (tuple, list)):
        return [_sanitize(v, depth + 1) for v in list(value)[:50]]
    if isinstance(value, dict):
        return {str(k): _sanitize(v, depth + 1) for k, v in list(value.items())[:200]}
    number = _number(value)  # IFDRational y similares
    return number if number is not None else str(value)[:200]


def _coordinate(values: Any, ref: Any, limit: float) -> float | None:
    """Convierte grados, minutos y segundos (con referencia N/S/E/W) a grados decimales."""
    if not isinstance(values, (tuple, list)) or len(values) != 3:
        return None
    parts = [_number(v) for v in values]
    if any(p is None for p in parts):
        return None
    degrees, minutes, seconds = parts
    if not (0 <= minutes < 60 and 0 <= seconds < 60):
        return None
    decimal = degrees + minutes / 60 + seconds / 3600
    sign = _text(ref).upper()
    if sign in ("S", "W"):
        decimal = -decimal
    elif sign not in ("N", "E"):
        return None
    return decimal if abs(decimal) <= limit else None


def _parse_exif_datetime(raw: str, offset: str) -> str:
    """Fecha EXIF «AAAA:MM:DD HH:MM:SS». Solo lleva zona horaria si OffsetTimeOriginal la indica."""
    try:
        parsed = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")  # noqa: DTZ007 - EXIF sin zona: naive a propósito
    except ValueError:
        return ""
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", offset or "")
    if match:
        sign = -1 if match.group(1) == "-" else 1
        delta = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
        return parsed.replace(tzinfo=timezone(sign * delta)).isoformat()
    return parsed.isoformat()


def _gps_timestamp(date_text: str, time_values: Any) -> str:
    if not date_text or not isinstance(time_values, (tuple, list)) or len(time_values) != 3:
        return ""
    parts = [_number(v) for v in time_values]
    if any(p is None for p in parts):
        return ""
    try:
        day = datetime.strptime(date_text, "%Y:%m:%d")  # noqa: DTZ007 - la zona se fija a UTC abajo
        moment = day.replace(hour=int(parts[0]), minute=int(parts[1]), second=int(parts[2]), tzinfo=UTC)
    except (ValueError, TypeError):
        return ""
    return moment.isoformat()


def _read_exif_ifds(exif: Image.Exif) -> tuple[dict, dict]:
    try:
        sub = dict(exif.get_ifd(_IFD_EXIF))
    except Exception:  # noqa: BLE001 - IFD malformado: se ignora, el resto del EXIF sigue disponible
        sub = {}
    try:
        gps = dict(exif.get_ifd(_IFD_GPS))
    except Exception:  # noqa: BLE001 - bloque GPS malformado: se trata como ausente
        gps = {}
    return sub, gps


def analyze_image(data: bytes, *, filename: str = "") -> ImageMetaResult:
    """Extrae EXIF, GPS y hashes de una imagen. Función sync: es cálculo puro."""
    if not data:
        raise InvalidInputError("no hay datos de imagen")
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidInputError(f"imagen demasiado grande (máximo {MAX_IMAGE_BYTES} bytes)")
    payload = bytes(data)
    sha = hashlib.sha256(payload).hexdigest()
    warnings: list[str] = []
    try:
        image = Image.open(io.BytesIO(payload))
        image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise InvalidInputError("el archivo no es una imagen válida o supera los límites de decodificación") from exc

    exif = image.getexif()
    sub, gps_ifd = _read_exif_ifds(exif)
    make = _text(exif.get(_TAG_MAKE))
    model = _text(exif.get(_TAG_MODEL))
    software = _text(exif.get(_TAG_SOFTWARE))
    taken_raw = _text(sub.get(_TAG_DATETIME_ORIGINAL)) or _text(exif.get(_TAG_DATETIME))
    offset = _text(sub.get(_TAG_OFFSET_TIME_ORIGINAL))
    taken_iso = _parse_exif_datetime(taken_raw, offset) if taken_raw else ""
    if taken_raw and not offset:
        warnings.append("fecha de captura sin zona horaria: el EXIF no indica el desfase")

    gps_point: GpsPoint | None = None
    if gps_ifd:
        lat = _coordinate(gps_ifd.get(_GPS_LAT), gps_ifd.get(_GPS_LAT_REF), 90)
        lon = _coordinate(gps_ifd.get(_GPS_LON), gps_ifd.get(_GPS_LON_REF), 180)
        if lat is None or lon is None:
            warnings.append("bloque GPS presente pero incompleto o inválido; no se extrajo ubicación")
        else:
            altitude = _number(gps_ifd.get(_GPS_ALT))
            if altitude is not None and _text(gps_ifd.get(_GPS_ALT_REF)) in ("1", "\x01"):
                altitude = -altitude
            gps_point = GpsPoint(
                lat=lat, lon=lon, altitude_m=altitude,
                timestamp_utc=_gps_timestamp(_text(gps_ifd.get(_GPS_DATE)), gps_ifd.get(_GPS_TIME)),
            )

    phash = ""
    try:
        phash = str(imagehash.phash(image))
    except Exception:  # noqa: BLE001 - imágenes con modos raros: el hash perceptual es opcional
        warnings.append("no se pudo calcular el hash perceptual de la imagen")

    raw: dict[str, Any] = {}
    for tag, value in exif.items():
        name = ExifTags.TAGS.get(tag, f"0x{tag:04X}")
        raw[name] = "<omitido>" if tag == _TAG_MAKER_NOTE else _sanitize(value)
    for tag, value in sub.items():
        name = ExifTags.TAGS.get(tag, f"0x{tag:04X}")
        raw.setdefault(name, "<omitido>" if tag == _TAG_MAKER_NOTE else _sanitize(value))
    if gps_ifd:
        raw["GPSInfo"] = {ExifTags.GPSTAGS.get(k, f"0x{k:04X}"): _sanitize(v) for k, v in gps_ifd.items()}

    doc_label = filename or f"imagen {sha[:12]}"
    entities = [
        EntityRecord(
            type="document", label=doc_label, ref="doc", confidence=1.0,
            props={
                "sha256": sha, "phash": phash, "format": image.format or "",
                "width": image.size[0], "height": image.size[1],
                "camera_make": make, "camera_model": model, "software": software,
                "taken_at": taken_iso or taken_raw,
            },
        ),
        EntityRecord(type="hash", label=sha, ref="sha256", props={"algorithm": "sha256"}, confidence=1.0),
    ]
    relations = [RelationRecord(src_ref="doc", dst_ref="sha256", type="identified_by", confidence=1.0)]
    if gps_point is not None:
        entities.append(EntityRecord(
            type="location", ref="gps", confidence=GPS_CONFIDENCE,
            label=f"{gps_point.lat:.6f}, {gps_point.lon:.6f}",
            props={
                "lat": gps_point.lat, "lon": gps_point.lon, "altitude_m": gps_point.altitude_m,
                "source": "EXIF GPS", "timestamp_utc": gps_point.timestamp_utc,
            },
        ))
        relations.append(RelationRecord(
            src_ref="doc", dst_ref="gps", type="taken_at", confidence=GPS_CONFIDENCE,
        ))

    return ImageMetaResult(
        filename=filename, sha256=sha, size_bytes=len(payload), format=image.format or "",
        width=image.size[0], height=image.size[1], mode=image.mode, phash=phash,
        camera_make=make, camera_model=model, software=software,
        taken_at=taken_raw, taken_at_iso=taken_iso, gps=gps_point,
        entities=entities, relations=relations, warnings=warnings, raw=raw,
    )


def reverse_image_links(image_url: str) -> list[ReverseSearchLink]:
    """Construye enlaces de búsqueda inversa para una URL de imagen. No consulta ningún servicio."""
    url = (image_url or "").strip()
    if not url or len(url) > MAX_URL_LEN or any(ord(ch) < 33 for ch in url):
        raise InvalidInputError("URL de imagen vacía, demasiado larga o con espacios")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise InvalidInputError("la URL de imagen debe ser http o https con host")
    encoded = quote(url, safe="")
    return [
        ReverseSearchLink(engine="google_lens", title="Google Lens",
                          url=f"https://lens.google.com/uploadbyurl?url={encoded}"),
        ReverseSearchLink(engine="yandex", title="Yandex Imágenes",
                          url=f"https://yandex.com/images/search?rpt=imageview&url={encoded}"),
        ReverseSearchLink(engine="tineye", title="TinEye",
                          url=f"https://tineye.com/search?url={encoded}"),
        ReverseSearchLink(engine="bing", title="Bing Visual Search",
                          url=f"https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{encoded}"),
    ]
