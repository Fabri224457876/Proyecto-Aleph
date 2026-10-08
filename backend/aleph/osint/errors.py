"""Excepciones de la caja de herramientas OSINT."""


class OsintError(Exception):
    """Base de todos los errores de la caja de herramientas."""


class InvalidInputError(OsintError, ValueError):
    """Entrada inválida (formato, rango, esquema de URL no permitido)."""


class UnsafeTargetError(InvalidInputError):
    """URL que apunta a una red privada, de loopback o local (protección SSRF)."""


class UnsafeFileError(InvalidInputError):
    """Archivo rechazado por límites de tamaño, zip bomb o XML con DTD/entidades."""


class UpstreamError(OsintError):
    """La API externa respondió con un error o con una respuesta inesperada."""

    def __init__(self, message: str, *, status_code: int | None = None, url: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class RateLimitedError(UpstreamError):
    """La API limitó las consultas (HTTP 429, o 403 en las APIs que lo usan para ello)."""

    def __init__(self, message: str, *, status_code: int | None = 429, url: str = "",
                 retry_after: float | None = None):
        super().__init__(message, status_code=status_code, url=url)
        self.retry_after = retry_after


class NetworkError(OsintError):
    """Fallo de transporte: conexión, TLS o tiempo de espera agotado."""


class DnsLookupError(OsintError):
    """Una consulta DNS falló por causa distinta de «sin registros» (p. ej. timeout)."""
