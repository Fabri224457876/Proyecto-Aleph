"""Recolección por conector desde la API: resolver el conector, filtrar parámetros y ejecutarlo.

Reglas de seguridad:
- Solo pasan los parámetros que el conector declara en `cls.params`. `path` y `data` nunca vienen
  del cliente: para los de importación la ruta la pone la API a partir del archivo subido. Así un
  pedido no puede hacer que el servidor lea un archivo arbitrario.
- Los archivos subidos quedan en `cases/<id>/uploads/<sha256>/<nombre>`: una carpeta por archivo,
  porque algunos importadores miran la carpeta padre del archivo.

La persistencia la hace `ingest_collection` (la misma que usa la ruta de colecciones ya normalizadas).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aleph.core.schemas import CollectionResult

from ..errors import EngineFailure, EngineUnavailable, Invalid, NotFound, ServiceError

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_CHUNK = 1024 * 1024
# Parámetros que solo puede fijar la API (con el archivo subido), nunca el cliente
FROM_UPLOAD_ONLY = frozenset({"path", "data"})


class TooLarge(ServiceError):
    status_code = 413


@dataclass(frozen=True)
class StoredUpload:
    path: Path
    name: str
    sha256: str
    size: int


def resolve_connector(name: str):
    """Clase del conector por nombre. Importar `aleph.connectors` registra todos."""
    try:
        import aleph.connectors  # noqa: F401  (al importarse registra los conectores)
        from aleph.connectors.base import ConnectorError, get_connector
    except ImportError as exc:
        raise EngineUnavailable("El módulo de conectores no está disponible en esta instalación.") from exc
    try:
        return get_connector(name.strip())
    except ConnectorError:
        raise NotFound(f"No existe el conector '{name}'. Consultá GET /api/connectors.") from None


def parse_params(raw: str) -> dict[str, Any]:
    text = (raw or "").strip() or "{}"
    try:
        value = json.loads(text)
    except ValueError:
        raise Invalid("El campo 'params' debe ser un objeto JSON válido.") from None
    if not isinstance(value, dict):
        raise Invalid("El campo 'params' debe ser un objeto JSON, p. ej. {\"handle\": \"ejemplo\"}.")
    return value


def declared_kwargs(cls, params: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Solo las claves que el conector declara. Devuelve (argumentos, claves ignoradas)."""
    kwargs: dict[str, Any] = {}
    ignored: list[str] = []
    for key, value in params.items():
        if key in cls.params and key not in FROM_UPLOAD_ONLY:
            kwargs[key] = value
        else:
            ignored.append(str(key))
    return kwargs, ignored


def check_required(cls, kwargs: dict[str, Any]) -> None:
    signature = inspect.signature(cls.collect)
    missing = [
        p.name for p in signature.parameters.values()
        if p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
        and p.name not in kwargs and p.name not in FROM_UPLOAD_ONLY
    ]
    if missing:
        raise Invalid(f"Faltan parámetros obligatorios para '{cls.name}': {', '.join(missing)}.")


def ensure_configured(cls, settings) -> None:
    missing = [key for key in cls.requires if not str(getattr(settings, key, "") or "").strip()]
    if missing:
        names = ", ".join(f"ALEPH_{key.upper()}" for key in missing)
        raise EngineUnavailable(f"El conector '{cls.name}' necesita configurar {names}.")


def _safe_name(filename: str | None) -> str:
    base = Path((filename or "").replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:120].strip("._")
    return cleaned or "archivo"


def store_upload(file_obj, filename: str | None, case_id: int, data_dir: str | Path) -> StoredUpload:
    """Escribe el archivo subido en su carpeta por hash y devuelve la ruta. Limita el tamaño."""
    base = Path(data_dir)
    upload_root = base / "cases" / str(case_id) / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    tmp = upload_root / f".upload-{uuid.uuid4().hex}.tmp"
    digest, size = hashlib.sha256(), 0
    try:
        with tmp.open("wb") as out:
            while chunk := file_obj.read(_CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise TooLarge("El archivo supera el tamaño máximo permitido (50 MB).")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise Invalid("El archivo está vacío.")
        sha = digest.hexdigest()
        name = _safe_name(filename)
        folder = upload_root / sha
        folder.mkdir(exist_ok=True)
        final = folder / name
        if final.exists():
            tmp.unlink()
        else:
            tmp.replace(final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return StoredUpload(path=final, name=name, sha256=sha, size=size)


def run_connector(cls, kwargs: dict[str, Any], *, client, settings) -> CollectionResult:
    """Ejecuta el conector. Cualquier fallo se informa como fallo del motor (502)."""

    async def _run() -> CollectionResult:
        connector = cls(client=client, settings=settings)
        return await connector.collect(**kwargs)

    try:
        return asyncio.run(_run())
    except ServiceError:
        raise
    except Exception as exc:  # un conector es código ajeno a la API: su fallo se informa tal cual
        message = str(exc) or type(exc).__name__
        raise EngineFailure(f"El conector '{cls.name}' falló: {message}") from exc
