from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..errors import NotFound

LIKE_ESCAPE = "\\"


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite devuelve fechas sin zona: en Aleph toda fecha guardada es UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def like_pattern(text: str) -> str:
    """Patrón para `columna.ilike(patron, escape=LIKE_ESCAPE)` que busca `text` literal."""
    escaped = text
    for char in (LIKE_ESCAPE, "%", "_"):
        escaped = escaped.replace(char, LIKE_ESCAPE + char)
    return f"%{escaped}%"


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def get_in_case(session: Session, model: Any, case_id: int, obj_id: int, what: str):
    """Devuelve el objeto solo si pertenece al caso. Un id de otro caso se trata como inexistente."""
    obj = session.get(model, obj_id)
    if obj is None or obj.case_id != case_id:
        raise NotFound(f"{what} {obj_id} no existe en este caso.")
    return obj
