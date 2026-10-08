"""Salida estructurada tolerante para modelos locales medianos.

Un modelo de 9B-27B cuantizado no siempre devuelve JSON limpio: antepone razonamiento
(`<think>…</think>`), envuelve el JSON en texto o en un bloque de código, deja comas colgando
o corta la respuesta por límite de tokens. Este módulo recupera lo recuperable y valida con
pydantic; lo que no valida se informa como error legible para reintentar con feedback.
"""

import ast
import json
import re
from typing import Annotated, Any, TypeVar

from pydantic import BaseModel, BeforeValidator, ValidationError

T = TypeVar("T", bound=BaseModel)

_REASONING_TAGS = "think|thinking|reasoning|razonamiento|thought"
_RE_REASONING_BLOCK = re.compile(rf"<({_REASONING_TAGS})>.*?</\1>", re.S | re.I)
_RE_REASONING_CLOSE = re.compile(rf"</(?:{_REASONING_TAGS})>", re.I)
_RE_REASONING_OPEN = re.compile(rf"<(?:{_REASONING_TAGS})>", re.I)
_RE_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n?(.*?)```", re.S)
_RE_TRAILING_COMMA = re.compile(r",\s*([}\]])")


class StructuredOutputError(ValueError):
    """La respuesta del modelo no se pudo convertir en el esquema esperado."""

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


def strip_reasoning(text: str) -> str:
    """Quita los bloques de razonamiento que algunos modelos emiten antes de responder."""
    text = _RE_REASONING_BLOCK.sub("", text or "")
    # Cierre huérfano (la plantilla se comió la apertura): vale lo que viene después.
    closes = list(_RE_REASONING_CLOSE.finditer(text))
    if closes:
        text = text[closes[-1].end():]
    # Apertura sin cierre (respuesta cortada en pleno razonamiento): no hay respuesta útil.
    opened = _RE_REASONING_OPEN.search(text)
    if opened:
        text = text[: opened.start()]
    return text.strip()


def _loads(candidate: str) -> Any:
    """json.loads con reparaciones baratas. Lanza ValueError si no hay caso."""
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    try:
        return json.loads(_RE_TRAILING_COMMA.sub(r"\1", candidate))
    except ValueError:
        pass
    try:  # dict con comillas simples / True / None al estilo Python
        value = ast.literal_eval(candidate)
    except (ValueError, SyntaxError, MemoryError, RecursionError) as exc:
        raise ValueError("no es JSON") from exc
    if isinstance(value, (dict, list)):
        return value
    raise ValueError("no es JSON")


def _scan(text: str, start: int) -> tuple[int | None, str | None]:
    """Recorre desde una llave/corchete de apertura.

    Devuelve (fin, None) si el valor cierra; (None, reparado) si quedó truncado pero se puede
    cerrar después del último elemento completo; (None, None) si no hay nada rescatable.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    last_good: tuple[int, tuple[str, ...]] | None = None
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack or (stack[-1] == "{") != (ch == "}"):
                return None, None
            stack.pop()
            if not stack:
                return i + 1, None
            last_good = (i + 1, tuple(stack))
    if last_good:
        pos, open_stack = last_good
        closers = "".join("}" if s == "{" else "]" for s in reversed(open_stack))
        return None, text[start:pos] + closers
    return None, None


def extract_json_candidates(text: str) -> list[Any]:
    """Todos los valores JSON (objetos o listas) que se pueden rescatar del texto, en orden."""
    found: list[Any] = []
    sources = [m.group(1) for m in _RE_FENCE.finditer(text)] + [text]
    for source in sources:
        i = 0
        while i < len(source):
            if source[i] not in "{[":
                i += 1
                continue
            end, repaired = _scan(source, i)
            candidate = source[i:end] if end is not None else repaired
            if candidate is not None:
                try:
                    found.append(_loads(candidate))
                except ValueError:
                    pass
                else:
                    if end is None:  # truncado: no queda nada más por leer
                        break
                    i = end
                    continue
            i += 1
    return found


def _expected_keys(schema: type[BaseModel]) -> set[str]:
    keys: set[str] = set()
    for name, field in schema.model_fields.items():
        keys.add(name)
        if isinstance(field.alias, str):
            keys.add(field.alias)
        choices = getattr(field.validation_alias, "choices", None)
        if choices:
            keys.update(c for c in choices if isinstance(c, str))
        elif isinstance(field.validation_alias, str):
            keys.add(field.validation_alias)
    return keys


def _short_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:6]:
        loc = ".".join(str(p) for p in err["loc"]) or "(raíz)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def parse_structured(raw: str, schema: type[T], *, wrap_list_as: str | None = None) -> T:
    """Extrae JSON de `raw` y lo valida contra `schema`.

    `wrap_list_as`: si el modelo devuelve una lista suelta de objetos, se envuelve bajo esa clave.
    """
    text = strip_reasoning(raw)
    if not text:
        raise StructuredOutputError("La respuesta llegó vacía (o solo con razonamiento).", raw)
    candidates = extract_json_candidates(text)
    if not candidates:
        raise StructuredOutputError("No se encontró ningún objeto JSON en la respuesta.", raw)
    expected = _expected_keys(schema)
    errors: list[str] = []
    # Primero los objetos: una lista suelta tipo "[1]" suele ser una nota al pie, no la respuesta.
    for cand in sorted(candidates, key=lambda c: not isinstance(c, dict)):
        if isinstance(cand, list):
            # Una lista sin ningún objeto adentro ("[1]") no es una respuesta.
            if not wrap_list_as or (cand and not any(isinstance(i, dict) for i in cand)):
                errors.append("Se esperaba un objeto JSON, no una lista.")
                continue
            cand = {wrap_list_as: cand}
        if not isinstance(cand, dict):
            continue
        if not expected & set(cand):
            # Envoltorio de un nivel: {"respuesta": {...}}
            inner = [v for v in cand.values() if isinstance(v, dict) and expected & set(v)]
            if len(inner) == 1:
                cand = inner[0]
            else:
                errors.append(
                    "El objeto no trae ninguna de las claves esperadas: "
                    + ", ".join(sorted(schema.model_fields))
                )
                continue
        try:
            return schema.model_validate(cand)
        except ValidationError as exc:
            errors.append(_short_error(exc))
    raise StructuredOutputError(errors[0] if errors else "JSON con forma inesperada.", raw)


# --- Piezas para armar esquemas tolerantes -------------------------------------------------


def lenient_list(item_model: type[BaseModel]) -> Any:
    """Tipo `list[item_model]` que descarta los elementos inválidos en vez de fallar entero.

    Un elemento mal formado entre veinte buenos no debe tirar toda la extracción.
    """

    def _keep_valid(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return []
        kept = []
        for item in value:
            if isinstance(item, item_model):
                kept.append(item)
                continue
            try:
                kept.append(item_model.model_validate(item))
            except ValidationError:
                continue
        return kept

    return Annotated[list[item_model], BeforeValidator(_keep_valid)]


_CONFIDENCE_WORDS = {
    "muy alta": 0.9, "alta": 0.85, "alto": 0.85, "high": 0.85,
    "media": 0.6, "medio": 0.6, "moderada": 0.6, "medium": 0.6,
    "baja": 0.35, "bajo": 0.35, "low": 0.35, "muy baja": 0.2,
}


def coerce_confidence(value: Any, default: float = 0.6) -> float:
    """Acepta 0.8, "0.8", "80%", 80, "alta"… y devuelve un número en [0, 1]."""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, str):
        text = value.strip().lower().replace(",", ".")
        if text in _CONFIDENCE_WORDS:
            return _CONFIDENCE_WORDS[text]
        percent = text.endswith("%")
        try:
            number = float(text.rstrip("% "))
        except ValueError:
            return default
        if percent:
            number /= 100
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return default
    if number != number:  # NaN
        return default
    if 1.0 < number <= 100.0:
        number /= 100
    return max(0.0, min(1.0, number))


def _to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return ""
    return str(value).strip()


Confidence = Annotated[float, BeforeValidator(coerce_confidence)]
LooseStr = Annotated[str, BeforeValidator(_to_str)]
