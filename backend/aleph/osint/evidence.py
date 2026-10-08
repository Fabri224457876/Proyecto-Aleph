"""Preservación de evidencia digital: contenido inmutable, SHA-256, manifiesto JSON y cadena de custodia.

- preserve_evidence guarda el contenido en una carpeta propia (archivo de solo lectura), calcula su SHA-256
  y escribe el manifiesto con la URL de origen, el sello de tiempo UTC y el primer evento de custodia.
- add_custody_event añade un evento (quién, cuándo, qué). Cada evento encadena el hash del anterior y lleva
  el SHA-256 del archivo, así que alterar el archivo, el manifiesto o cualquier evento se detecta.
- verify_evidence recalcula todo y devuelve los problemas encontrados.

Limitaciones que conviene conocer:
- El sello de tiempo es el reloj del equipo en UTC. No es un sello de tiempo cualificado (RFC 3161) ni prueba
  la hora real por sí mismo.
- La cadena detecta alteraciones posteriores. Quien controla la carpeta puede regenerar toda la cadena; para
  una prueba fuerte hay que registrar el último hash en un medio externo.
- Esta función no descarga nada: recibe el contenido ya obtenido y su URL de origen.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from .errors import OsintError

EVIDENCE_VERSION = 1
MANIFEST_NAME = "manifest.json"
PAYLOAD_NAME = "original.bin"
GENESIS = "0" * 64
_ID_RE = re.compile(r"[a-f0-9]{32}")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class EvidenceError(OsintError):
    """Error al preservar o leer una evidencia."""


class EvidenceIntegrityError(EvidenceError):
    """La evidencia no coincide con su manifiesto; no se registra nada más sobre ella."""


class CustodyEvent(BaseModel):
    seq: int
    at: str  # ISO 8601 en UTC
    by: str
    action: str  # collected, reviewed, exported, ...
    detail: str = ""
    file_sha256: str
    prev_hash: str
    hash: str


class EvidenceFile(BaseModel):
    name: str
    size: int
    sha256: str


class EvidenceManifest(BaseModel):
    version: int = EVIDENCE_VERSION
    evidence_id: str
    source_url: str
    description: str = ""
    file: EvidenceFile
    custody: list[CustodyEvent] = Field(default_factory=list)


class VerificationResult(BaseModel):
    ok: bool
    evidence_id: str
    problems: list[str] = Field(default_factory=list)
    sha256_actual: str = ""
    events: int = 0


def _clock(now: Callable[[], datetime] | None) -> str:
    moment = now() if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        raise EvidenceError("el reloj debe devolver una fecha con zona horaria")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _event_digest(seq: int, at: str, by: str, action: str, detail: str, file_sha256: str,
                  prev_hash: str) -> str:
    payload = json.dumps(
        {"seq": seq, "at": at, "by": by, "action": action, "detail": detail,
         "file_sha256": file_sha256, "prev_hash": prev_hash},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _make_event(seq: int, at: str, by: str, action: str, detail: str, file_sha256: str,
                prev_hash: str) -> CustodyEvent:
    digest = _event_digest(seq, at, by, action, detail, file_sha256, prev_hash)
    return CustodyEvent(seq=seq, at=at, by=by, action=action, detail=detail,
                        file_sha256=file_sha256, prev_hash=prev_hash, hash=digest)


def _clean_text(value: str, label: str, limit: int) -> str:
    text = (value or "").strip()
    if not text or len(text) > limit or _CONTROL_RE.search(text):
        raise EvidenceError(f"{label} vacío, demasiado largo o con caracteres de control")
    return text


def _write_manifest(item_dir: Path, manifest: EvidenceManifest) -> None:
    temp = item_dir / (MANIFEST_NAME + ".tmp")
    temp.write_text(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    os.replace(temp, item_dir / MANIFEST_NAME)


def load_manifest(item_dir: str | Path) -> EvidenceManifest:
    path = Path(item_dir) / MANIFEST_NAME
    try:
        return EvidenceManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise EvidenceError(f"manifiesto ilegible en {path.name}") from exc


def preserve_evidence(
    content: bytes,
    *,
    source_url: str,
    collected_by: str,
    storage_dir: str | Path,
    description: str = "",
    filename: str = "",
    evidence_id: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> EvidenceManifest:
    """Guarda el contenido en storage_dir/<evidence_id>/ y devuelve el manifiesto. Sin red."""
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise EvidenceError("el contenido a preservar está vacío")
    source = _clean_text(source_url, "la URL de origen", 2048)
    collector = _clean_text(collected_by, "el responsable de la recolección", 200)
    eid = evidence_id or uuid.uuid4().hex
    if not _ID_RE.fullmatch(eid):
        raise EvidenceError("identificador de evidencia inválido")
    # El sello se calcula antes de tocar el disco: un reloj inválido no deja una evidencia a medias
    collected_at = _clock(now)
    item_dir = Path(storage_dir) / eid
    try:
        item_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise EvidenceError(f"ya existe una evidencia con identificador {eid}") from exc

    payload = bytes(content)
    digest = hashlib.sha256(payload).hexdigest()
    with open(item_dir / PAYLOAD_NAME, "xb") as handle:
        handle.write(payload)
    display_name = _SAFE_NAME_RE.sub("_", Path(filename or "contenido.bin").name)[:120] or "contenido.bin"
    event = _make_event(1, collected_at, collector, "collected", "contenido recogido y preservado", digest,
                        GENESIS)
    manifest = EvidenceManifest(
        evidence_id=eid, source_url=source, description=description.strip(),
        file=EvidenceFile(name=display_name, size=len(payload), sha256=digest), custody=[event],
    )
    _write_manifest(item_dir, manifest)
    try:
        os.chmod(item_dir / PAYLOAD_NAME, 0o444)
    except OSError:  # en Windows solo cambia el atributo de lectura; no es fatal
        pass
    return manifest


def add_custody_event(
    item_dir: str | Path,
    *,
    by: str,
    action: str,
    detail: str = "",
    now: Callable[[], datetime] | None = None,
) -> EvidenceManifest:
    """Añade un evento a la cadena. Antes verifica la evidencia y se niega si está alterada."""
    result = verify_evidence(item_dir)
    if not result.ok:
        raise EvidenceIntegrityError("la evidencia no pasa la verificación; no se añaden eventos: "
                                     + "; ".join(result.problems))
    manifest = load_manifest(item_dir)
    last = manifest.custody[-1]
    event = _make_event(
        last.seq + 1, _clock(now), _clean_text(by, "el responsable", 200),
        _clean_text(action, "la acción", 60), detail.strip(), manifest.file.sha256, last.hash,
    )
    manifest.custody.append(event)
    _write_manifest(Path(item_dir), manifest)
    return manifest


def _parse_utc(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.utcoffset() != UTC.utcoffset(None):
        return None
    return moment


def verify_evidence(item_dir: str | Path) -> VerificationResult:
    """Recalcula el hash del archivo y verifica manifiesto, secuencia, enlaces y hashes de la cadena."""
    folder = Path(item_dir)
    try:
        manifest = load_manifest(folder)
    except EvidenceError as exc:
        return VerificationResult(ok=False, evidence_id=folder.name, problems=[str(exc)])
    problems: list[str] = []
    if manifest.version != EVIDENCE_VERSION:
        problems.append(f"versión de manifiesto no soportada: {manifest.version}")
    if folder.name != manifest.evidence_id:
        problems.append("el identificador del manifiesto no coincide con el nombre de la carpeta")

    actual = ""
    payload_path = folder / PAYLOAD_NAME
    if not payload_path.is_file():
        problems.append("falta el archivo de evidencia")
    else:
        actual = hashlib.sha256(payload_path.read_bytes()).hexdigest()
        if actual != manifest.file.sha256:
            problems.append("el SHA-256 del archivo no coincide con el manifiesto (archivo alterado)")
        if payload_path.stat().st_size != manifest.file.size:
            problems.append("el tamaño del archivo no coincide con el manifiesto")

    if not manifest.custody:
        problems.append("la cadena de custodia está vacía")
    previous_hash = GENESIS
    previous_time: datetime | None = None
    for index, event in enumerate(manifest.custody, start=1):
        if event.seq != index:
            problems.append(f"evento {index}: número de secuencia inconsistente")
        if event.prev_hash != previous_hash:
            problems.append(f"evento {index}: el enlace con el evento anterior está roto")
        expected = _event_digest(event.seq, event.at, event.by, event.action, event.detail,
                                 event.file_sha256, event.prev_hash)
        if event.hash != expected:
            problems.append(f"evento {index}: el hash no coincide con su contenido (evento alterado)")
        if event.file_sha256 != manifest.file.sha256:
            problems.append(f"evento {index}: el hash del archivo difiere del manifiesto")
        moment = _parse_utc(event.at)
        if moment is None:
            problems.append(f"evento {index}: el sello de tiempo no está en UTC")
        elif previous_time is not None and moment < previous_time:
            problems.append(f"evento {index}: el sello de tiempo retrocede")
        previous_time = moment or previous_time
        previous_hash = event.hash
    if manifest.custody and manifest.custody[0].action != "collected":
        problems.append("el primer evento de la cadena no es la recolección")
    return VerificationResult(
        ok=not problems, evidence_id=manifest.evidence_id, problems=problems,
        sha256_actual=actual, events=len(manifest.custody),
    )
