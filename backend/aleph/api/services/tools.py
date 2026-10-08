"""Caja de herramientas OSINT desde la API: ejecutar una herramienta del registro y devolver su resultado.

El registro (`aleph.osint.registry`) dice qué herramienta existe y dónde está su función. Acá se
arman los argumentos a partir de la firma real de la función: el dato a analizar va al primer
parámetro posicional (o el archivo, si ese parámetro recibe bytes), y solo se pasan los parámetros
con nombre que la función declara. `client`, `resolver` y `storage_dir` no los elige el cliente:
los pone la API (o quedan en su valor por defecto).

No persiste nada: lo que devuelve la herramienta lo guarda la ruta, como propuestas.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any

from aleph.core.schemas import EntityRecord, RelationRecord
from aleph.osint.errors import InvalidInputError, OsintError
from aleph.osint.evidence import EvidenceError
from aleph.osint.registry import ToolSpec, get_tool, list_tools, resolve_entrypoint

from ..errors import EngineFailure, Invalid, NotFound, ServiceError
from .collect import TooLarge

# Parámetros que no elige el cliente: los inyecta la API o son internos de la herramienta
FORBIDDEN_PARAMS = frozenset({"client", "resolver", "storage_dir", "now"})
# Primer argumento que recibe bytes (el archivo subido), no texto
BYTES_ARGS = frozenset({"data", "content"})
MAX_TARGET_CHARS = 2048
_POSITIONAL = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


@dataclass
class ToolRun:
    tool: str
    result: dict[str, Any]
    entities: list[EntityRecord] = field(default_factory=list)
    relations: list[RelationRecord] = field(default_factory=list)
    ignored_params: list[str] = field(default_factory=list)


def catalog() -> list[ToolSpec]:
    return list_tools()


def tool_spec(name: str) -> ToolSpec:
    try:
        return get_tool(name.strip())
    except KeyError:
        raise NotFound(f"No existe la herramienta '{name}'. Consultá GET /api/tools.") from None


def _record(item: Any, cls: type) -> Any:
    return item if isinstance(item, cls) else cls.model_validate(item)


def run_tool(
    name: str,
    *,
    target: str,
    params: dict[str, Any],
    payload: bytes | None,
    filename: str,
    actor: str,
    storage_dir: str,
    client: Any = None,
) -> ToolRun:
    spec = tool_spec(name)
    text = (target or "").strip()
    if len(text) > MAX_TARGET_CHARS:
        raise Invalid(f"El dato a analizar supera los {MAX_TARGET_CHARS} caracteres.")
    fn = resolve_entrypoint(spec)
    signature = inspect.signature(fn)
    accepted = signature.parameters

    kwargs: dict[str, Any] = {}
    ignored: list[str] = []
    for key, value in params.items():
        param = accepted.get(key)
        if param is None or key in FORBIDDEN_PARAMS or param.kind not in (*_POSITIONAL, inspect.Parameter.KEYWORD_ONLY):
            ignored.append(str(key))
        else:
            kwargs[key] = value

    args: list[Any] = []
    positional = [p for p in accepted.values() if p.kind in _POSITIONAL]
    takes_bytes = bool(positional) and positional[0].name in BYTES_ARGS
    if payload is not None and not takes_bytes and "filename" not in accepted:
        raise Invalid(f"La herramienta '{spec.name}' no recibe archivos: quitá el campo 'file'.")
    if positional:
        first = positional[0]
        if takes_bytes:
            if payload is None:
                raise Invalid(f"La herramienta '{spec.name}' necesita un archivo: subilo en el campo 'file'.")
            args.append(payload)
            kwargs.pop(first.name, None)
        elif text:
            args.append(text)
        elif first.default is inspect.Parameter.empty:
            raise Invalid(f"La herramienta '{spec.name}' necesita el dato a analizar (campo 'target').")
    if payload is not None and "filename" in accepted:
        kwargs.setdefault("filename", filename)
    if "source_url" in accepted:
        kwargs.setdefault("source_url", text)
    if "collected_by" in accepted:
        kwargs["collected_by"] = actor
    if "storage_dir" in accepted:
        kwargs["storage_dir"] = storage_dir
    if "client" in accepted and client is not None:
        kwargs["client"] = client

    try:
        signature.bind(*args, **kwargs)
    except TypeError as exc:
        raise Invalid(f"Faltan datos para '{spec.name}': {exc}.") from None

    try:
        raw = asyncio.run(fn(*args, **kwargs)) if inspect.iscoroutinefunction(fn) else fn(*args, **kwargs)
    except (InvalidInputError, EvidenceError) as exc:
        raise Invalid(str(exc)) from exc
    except OsintError as exc:
        raise EngineFailure(f"La herramienta '{spec.name}' no pudo completar la consulta: {exc}") from exc
    except ServiceError:
        raise
    except ValueError as exc:
        raise Invalid(str(exc)) from exc

    result = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else {"valor": raw}
    entities = [_record(e, EntityRecord) for e in (getattr(raw, "entities", None) or [])]
    relations = [_record(r, RelationRecord) for r in (getattr(raw, "relations", None) or [])]
    return ToolRun(tool=spec.name, result=result, entities=entities, relations=relations,
                   ignored_params=ignored)


def read_limited(file_obj, limit: int) -> bytes:
    """Lee un archivo subido completo, con tope de tamaño."""
    data = file_obj.read(limit + 1)
    if len(data) > limit:
        raise TooLarge(f"El archivo supera el tamaño máximo permitido ({limit // (1024 * 1024)} MB).")
    if not data:
        raise Invalid("El archivo está vacío.")
    return data
