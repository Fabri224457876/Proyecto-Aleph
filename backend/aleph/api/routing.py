"""Clase de ruta común: errores en español con forma uniforme `{"detail": "..."}`."""

from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .errors import ServiceError
from .schemas import Message, ValidationErrorOut

_MESSAGES = {
    "missing": "Campo obligatorio.",
    "string_too_short": "El texto es demasiado corto.",
    "string_too_long": "El texto es demasiado largo.",
    "string_type": "Debe ser un texto.",
    "string_pattern_mismatch": "El formato no es válido.",
    "int_parsing": "Debe ser un número entero.",
    "int_type": "Debe ser un número entero.",
    "float_parsing": "Debe ser un número.",
    "float_type": "Debe ser un número.",
    "bool_parsing": "Debe ser verdadero o falso.",
    "bool_type": "Debe ser verdadero o falso.",
    "greater_than_equal": "El valor es menor que el mínimo permitido.",
    "less_than_equal": "El valor es mayor que el máximo permitido.",
    "greater_than": "El valor es menor que el mínimo permitido.",
    "less_than": "El valor es mayor que el máximo permitido.",
    "json_invalid": "El cuerpo no es JSON válido.",
    "dict_type": "Debe ser un objeto.",
    "list_type": "Debe ser una lista.",
    "datetime_parsing": "Fecha inválida (usá ISO 8601).",
    "datetime_from_date_parsing": "Fecha inválida (usá ISO 8601).",
    "model_attributes_type": "Debe ser un objeto.",
    "extra_forbidden": "Campo no permitido.",
}


def _translate(error: dict) -> str:
    kind = error.get("type", "")
    if kind == "value_error":
        return str(error.get("msg", "")).removeprefix("Value error, ")
    if kind in ("literal_error", "enum"):
        expected = (error.get("ctx") or {}).get("expected", "")
        return f"Valor no permitido. Opciones: {expected}."
    return _MESSAGES.get(kind, str(error.get("msg", "Valor inválido.")))


def validation_payload(exc: RequestValidationError) -> dict:
    errors = [
        {"field": ".".join(str(part) for part in err.get("loc", ())), "message": _translate(err)}
        for err in exc.errors()
    ]
    summary = "; ".join(f"{e['field']}: {e['message']}" for e in errors[:5])
    return {"detail": f"Datos inválidos. {summary}".strip(), "errors": errors}


class AlephRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def spanish_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return JSONResponse(status_code=422, content=validation_payload(exc))
            except ServiceError as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

        return spanish_handler


COMMON_RESPONSES = {
    401: {"model": Message, "description": "Falta el token o no es válido"},
    403: {"model": Message, "description": "El rol no permite la acción"},
    404: {"model": Message, "description": "No existe (o no pertenece al caso)"},
    409: {"model": Message, "description": "Conflicto con el estado actual"},
    422: {"model": ValidationErrorOut, "description": "Datos inválidos"},
}


def make_router(**kwargs) -> APIRouter:
    kwargs.setdefault("route_class", AlephRoute)
    kwargs.setdefault("responses", COMMON_RESPONSES)
    return APIRouter(**kwargs)
