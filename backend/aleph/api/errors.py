"""Errores de la capa de servicios. No dependen de FastAPI; las rutas los traducen a HTTP."""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Invalid(ServiceError):
    status_code = 400


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


class EngineFailure(ServiceError):
    """Un motor (MENARD, FUNES, CTI) falló al ejecutarse."""

    status_code = 502


class EngineUnavailable(ServiceError):
    """Un motor todavía no está instalado en este despliegue."""

    status_code = 503


class Busy(ServiceError):
    status_code = 503
