"""Orquestador de enriquecimiento: corre en paralelo los proveedores que aplican al tipo.

Un proveedor que falla, se cuelga o lanza una excepción inesperada no tira abajo a los demás:
su resultado queda con status `error` y el motivo.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from .base import EnrichmentProvider, EnrichmentResult, Verdict, indicator_value
from .crtsh import CrtSh
from .hibp import Hibp
from .malwarebazaar import MalwareBazaar
from .otx import Otx
from .rdap import Rdap
from .shodan_internetdb import ShodanInternetDB
from .threatfox import ThreatFox
from .urlhaus import URLhaus
from .virustotal import VirusTotal

DEFAULT_PROVIDERS: tuple[type[EnrichmentProvider], ...] = (
    ShodanInternetDB, URLhaus, ThreatFox, MalwareBazaar, CrtSh, Rdap, VirusTotal, Otx, Hibp,
)
_VERDICT_RANK: dict[str, int] = {"unknown": 0, "benign": 1, "suspicious": 2, "malicious": 3}


class EnrichmentReport(BaseModel):
    indicator_type: str
    indicator: str
    results: list[EnrichmentResult] = Field(default_factory=list)
    message: str = ""

    @property
    def verdict(self) -> Verdict:
        """Veredicto más severo entre los proveedores que respondieron (status ok)."""
        verdicts = [r.verdict for r in self.results if r.status == "ok"]
        return max(verdicts, key=_VERDICT_RANK.__getitem__, default="unknown")  # type: ignore[return-value]

    @property
    def entities(self) -> list[EntityRecord]:
        """Entidades nuevas de todos los proveedores, sin repetir (tipo, valor)."""
        merged: dict[tuple[str, str], EntityRecord] = {}
        for result in self.results:
            for ent in result.entities:
                merged.setdefault((ent.type, ent.label.lower()), ent)
        return list(merged.values())

    @property
    def relations(self) -> list[RelationRecord]:
        merged: dict[tuple[str, str, str], RelationRecord] = {}
        for result in self.results:
            for rel in result.relations:
                merged.setdefault((rel.src_ref, rel.dst_ref, rel.type), rel)
        return list(merged.values())


def _failed(provider: EnrichmentProvider, entity: EntityRecord, message: str) -> EnrichmentResult:
    return EnrichmentResult(provider=provider.name, indicator_type=entity.type,
                            indicator=indicator_value(entity), status="error",
                            message=f"{provider.title}: {message}",
                            retrieved_at=datetime.now(UTC))


async def _guarded(provider: EnrichmentProvider, entity: EntityRecord,
                   timeout: float) -> EnrichmentResult:
    try:
        return await asyncio.wait_for(provider.enrich(entity), timeout=timeout)
    except TimeoutError:
        return _failed(provider, entity, "tiempo total agotado")
    except Exception as exc:  # un proveedor roto no debe tumbar a los demás
        return _failed(provider, entity, f"fallo interno ({type(exc).__name__})")


async def enrich_indicator(
    entity: EntityRecord,
    providers: Sequence[EnrichmentProvider] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    timeout: float = 15.0,
) -> EnrichmentReport:
    """Consulta en paralelo los proveedores aplicables a `entity` y junta los resultados.

    Si `providers` es None se usan todos los proveedores por defecto, construidos con `client`
    (o con un AsyncClient propio si no se pasa). Los que no tienen clave responden
    `unavailable` sin hacer llamadas.
    """
    if providers is None:
        if client is None:
            async with httpx.AsyncClient() as own_client:
                return await enrich_indicator(
                    entity, [cls(own_client) for cls in DEFAULT_PROVIDERS], timeout=timeout)
        providers = [cls(client) for cls in DEFAULT_PROVIDERS]

    applicable = [p for p in providers if p.supports(entity)]
    results = await asyncio.gather(*(_guarded(p, entity, timeout) for p in applicable))
    message = "" if applicable else f"Ningún proveedor consulta indicadores de tipo {entity.type}."
    return EnrichmentReport(indicator_type=entity.type, indicator=indicator_value(entity),
                            results=list(results), message=message)
