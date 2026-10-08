"""AlienVault OTX: pulsos (reportes de la comunidad) donde aparece un indicador. Con clave.

Verificado en el SDK oficial (AlienVault-OTX/OTX-Python-SDK): header `X-OTX-API-KEY`, ruta
`/api/v1/indicators/{tipo}/{valor}/general`, con tipos IPv4, IPv6, domain, url, file y cve.
Los nombres de campo de la respuesta (pulse_info.count, pulses[]) son los de la API pública
y no se verificaron contra una respuesta real.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from aleph.core.schemas import EntityRecord

from .base import (
    REL_ASSOCIATED_WITH,
    EnrichmentProvider,
    EnrichmentResult,
    indicator_ref,
    indicator_value,
    ip_version,
    new_entity,
    new_relation,
)

API_ROOT = "https://otx.alienvault.com/api/v1/indicators"
MAX_FAMILIES = 10


def _otx_type(entity: EntityRecord) -> str | None:
    if entity.type == "ip":
        return "IPv6" if ip_version(entity) == 6 else "IPv4"
    return {"domain": "domain", "url": "url", "hash": "file", "vulnerability": "cve"}.get(
        entity.type)


def _family_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("display_name") or item.get("name") or item.get("id") or "").strip()
    return ""


class Otx(EnrichmentProvider):
    name = "otx"
    title = "AlienVault OTX"
    applies_to = frozenset({"ip", "domain", "url", "hash", "vulnerability"})
    key_setting = "otx_api_key"

    async def _run(self, indicator: EntityRecord) -> EnrichmentResult:
        otx_type = _otx_type(indicator)
        if otx_type is None:
            return self._result(indicator, "unsupported", message="Tipo sin endpoint en OTX.")
        value = indicator_value(indicator)
        url = f"{API_ROOT}/{otx_type}/{quote(value, safe='')}/general"
        response = await self._request("GET", url, headers={"X-OTX-API-KEY": self.api_key()})
        if response.status_code == 404:
            return self._result(indicator, "no_data", message="OTX no conoce este indicador.")
        self._check(response)
        body = self._json(response)

        pulse_info = body.get("pulse_info") or {}
        count = int(pulse_info.get("count", 0))
        pulses = [p for p in pulse_info.get("pulses") or [] if isinstance(p, dict)]
        labels = [f"otx:pulsos={count}"]
        adversaries = sorted({str(p["adversary"]).strip() for p in pulses if p.get("adversary")})
        labels += [f"otx:adversario={a}" for a in adversaries[:5]]
        labels += [f"otx:pulso={p.get('name', '')}" for p in pulses[:3] if p.get("name")]
        if body.get("reputation") is not None:
            labels.append(f"otx:reputacion={body['reputation']}")

        ref = indicator_ref(indicator)
        families = sorted({_family_name(f) for p in pulses
                           for f in (p.get("malware_families") or []) if _family_name(f)})
        entities: list[EntityRecord] = []
        relations = []
        for family in families[:MAX_FAMILIES]:
            ent = new_entity("malware", family, {"source": "otx"}, confidence=0.6)
            entities.append(ent)
            relations.append(new_relation(ref, ent.ref, REL_ASSOCIATED_WITH, 0.6))

        return self._result(
            indicator, "ok", verdict="suspicious" if count > 0 else "unknown", labels=labels,
            entities=entities, relations=relations, raw=body,
            message=f"Aparece en {count} pulsos de la comunidad OTX.",
        )
