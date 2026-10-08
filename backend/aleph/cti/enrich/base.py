"""Interfaz común de los proveedores de enriquecimiento de IOCs.

Cada proveedor implementa `_run(indicator)`. La clase base se encarga del resto: clave
ausente (status `unavailable`, sin llamar a la red), tipos no soportados, 429 (status
`rate_limited` con `retry_after`, sin reintentar solo), timeouts y errores de red, que nunca
escapan hacia el llamador. Todos los proveedores devuelven `EnrichmentResult`.
"""

from __future__ import annotations

import abc
import ipaddress
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

Verdict = Literal["malicious", "suspicious", "benign", "unknown"]
Status = Literal["ok", "no_data", "unavailable", "rate_limited", "error", "unsupported"]

# Solo ASCII: httpx rechaza cabeceras con caracteres fuera de ese rango.
USER_AGENT = "Aleph-CTI/0.1 (modulo CTI; consultas de investigacion)"

# Tipos de relación que usan los proveedores (vocabulario abierto del grafo de Aleph).
REL_RESOLVES_TO = "resolves_to"
REL_SUBDOMAIN_OF = "subdomain_of"
REL_REGISTERED_BY = "registered_by"
REL_HAS_VULNERABILITY = "has_vulnerability"
REL_HOSTED_ON = "hosted_on"
REL_DELIVERS = "delivers"
REL_ASSOCIATED_WITH = "associated_with"
REL_EXPOSED_IN = "exposed_in"


class EnrichmentResult(BaseModel):
    provider: str
    indicator_type: str
    indicator: str
    status: Status
    verdict: Verdict = "unknown"
    labels: list[str] = Field(default_factory=list)
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    raw: Any = None  # respuesta cruda de la API, para guardarla como evidencia
    message: str = ""  # en español, para el analista
    retry_after: float | None = None  # segundos, cuando el status es rate_limited
    retrieved_at: datetime


class ProviderError(Exception):
    """Error controlado de un proveedor. Se convierte en status `error`."""


class RateLimited(ProviderError):
    def __init__(self, retry_after: float | None):
        super().__init__("límite de consultas alcanzado")
        self.retry_after = retry_after


def indicator_value(entity: EntityRecord) -> str:
    return str(entity.props.get("value") or entity.label).strip()


def indicator_ref(entity: EntityRecord) -> str:
    return entity.ref or f"{entity.type}:{indicator_value(entity)}"


def hash_algorithm(entity: EntityRecord) -> str | None:
    """Algoritmo del hash: el de props o, si falta, el que corresponde a su longitud."""
    declared = str(entity.props.get("algorithm", "")).upper().replace("SHA1", "SHA-1")
    if declared in ("MD5", "SHA-1", "SHA-256"):
        return declared
    return {32: "MD5", 40: "SHA-1", 64: "SHA-256"}.get(len(indicator_value(entity)))


def ip_version(entity: EntityRecord) -> int | None:
    try:
        return ipaddress.ip_address(indicator_value(entity)).version
    except ValueError:
        return None


def new_entity(kind: str, label: str, props: dict[str, Any] | None = None,
               confidence: float = 0.8) -> EntityRecord:
    """Entidad nueva creada por un proveedor. El ref es determinista (tipo:valor)."""
    value = label.strip()
    return EntityRecord(type=kind, label=value, props={"value": value, **(props or {})},
                        confidence=confidence, ref=f"{kind}:{value.lower()}")


def new_relation(src_ref: str, dst_ref: str, rel_type: str,
                 confidence: float = 0.8) -> RelationRecord:
    return RelationRecord(src_ref=src_ref, dst_ref=dst_ref, type=rel_type, confidence=confidence)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # formato fecha HTTP: no lo interpretamos


class EnrichmentProvider(abc.ABC):
    """Proveedor de enriquecimiento. Subclases: definir atributos de clase y `_run`."""

    name: ClassVar[str]  # identificador estable, p. ej. "virustotal"
    title: ClassVar[str]  # nombre visible
    applies_to: ClassVar[frozenset[str]]  # tipos de ENTITY_TYPES que consulta
    key_setting: ClassVar[str | None] = None  # nombre del setting de la clave, si la requiere
    default_timeout: ClassVar[float] = 10.0

    def __init__(self, client: httpx.AsyncClient | None = None, *, api_key: str | None = None,
                 timeout: float | None = None):
        """`client` permite inyectar un AsyncClient (p. ej. con MockTransport en tests).
        `api_key`: None lee el setting; "" fuerza el modo sin clave."""
        self._client = client
        self._api_key = api_key
        self._timeout = timeout or self.default_timeout

    # -- API pública --------------------------------------------------------

    def supports(self, entity: EntityRecord) -> bool:
        return entity.type in self.applies_to

    def api_key(self) -> str:
        if self._api_key is not None:
            return self._api_key.strip()
        if not self.key_setting:
            return ""
        from aleph.core.config import get_settings  # import tardío: no leer .env al importar

        return str(getattr(get_settings(), self.key_setting, "") or "").strip()

    async def enrich(self, indicator: EntityRecord) -> EnrichmentResult:
        """Consulta el proveedor. Nunca lanza excepciones: los errores van en el resultado."""
        if not self.supports(indicator):
            return self._result(indicator, "unsupported",
                                message=f"{self.title} no consulta indicadores de tipo "
                                        f"{indicator.type}")
        if self.key_setting and not self.api_key():
            return self._result(
                indicator, "unavailable",
                message=f"{self.title} requiere clave: configurá ALEPH_{self.key_setting.upper()}.",
            )
        try:
            return await self._run(indicator)
        except RateLimited as exc:
            return self._result(indicator, "rate_limited", retry_after=exc.retry_after,
                                message=f"{self.title}: {exc}")
        except ProviderError as exc:
            return self._result(indicator, "error", message=f"{self.title}: {exc}")
        except httpx.TimeoutException:
            return self._result(indicator, "error",
                                message=f"{self.title}: tiempo de espera agotado")
        except httpx.HTTPError as exc:
            return self._result(indicator, "error",
                                message=f"{self.title}: error de red ({type(exc).__name__})")

    # -- para las subclases -------------------------------------------------

    @abc.abstractmethod
    async def _run(self, indicator: EntityRecord) -> EnrichmentResult:
        """Consulta la API y arma el resultado. Puede lanzar ProviderError o httpx.HTTPError."""

    def _result(self, indicator: EntityRecord, status: Status, *, verdict: Verdict = "unknown",
                labels: list[str] | tuple[str, ...] = (),
                entities: list[EntityRecord] | tuple[EntityRecord, ...] = (),
                relations: list[RelationRecord] | tuple[RelationRecord, ...] = (),
                raw: Any = None, message: str = "",
                retry_after: float | None = None) -> EnrichmentResult:
        return EnrichmentResult(
            provider=self.name,
            indicator_type=indicator.type,
            indicator=indicator_value(indicator),
            status=status,
            verdict=verdict,
            labels=list(labels),
            entities=list(entities),
            relations=list(relations),
            raw=raw,
            message=message,
            retry_after=retry_after,
            retrieved_at=datetime.now(UTC),
        )

    async def _request(self, method: str, url: str, *, headers: dict[str, str] | None = None,
                       params: dict[str, Any] | None = None, data: dict[str, Any] | None = None,
                       json: Any = None, follow_redirects: bool = False) -> httpx.Response:
        merged = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
        kwargs: dict[str, Any] = {
            "headers": merged,
            "params": params,
            "data": data,
            "json": json,
            "timeout": self._timeout,
            "follow_redirects": follow_redirects,
        }
        if self._client is not None:
            return await self._client.request(method, url, **kwargs)
        async with httpx.AsyncClient() as client:
            return await client.request(method, url, **kwargs)

    def _check(self, response: httpx.Response) -> None:
        """Convierte códigos de error en excepciones. El 404 lo maneja cada proveedor."""
        code = response.status_code
        if code == 429:
            raise RateLimited(_retry_after(response))
        if code in (401, 403):
            raise ProviderError(f"acceso rechazado (HTTP {code}): revisá la clave o los permisos")
        if code >= 500:
            raise ProviderError(f"servicio no disponible (HTTP {code})")
        if code >= 400 and code != 404:
            raise ProviderError(f"respuesta inesperada (HTTP {code})")

    def _json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            raise ProviderError("la respuesta no es JSON válido") from None
