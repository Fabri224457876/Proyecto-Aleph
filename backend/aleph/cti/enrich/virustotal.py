"""VirusTotal API v3: detecciones de motores antivirus para IP, dominio, URL o hash. Con clave.

Verificado: header `x-apikey`; rutas /ip_addresses/{ip}, /domains/{dominio}, /files/{hash} y
/urls/{id}, donde id es el URL en base64 url-safe sin relleno. El esquema de respuesta
(data.attributes.last_analysis_stats) sale de la referencia pública de la API; la
documentación consultada no mostró un ejemplo completo.
"""

from __future__ import annotations

import base64
from typing import Any

from aleph.core.schemas import EntityRecord

from .base import EnrichmentProvider, EnrichmentResult, Verdict, indicator_value

MALICIOUS_THRESHOLD = 3  # detecciones de malicioso para considerarlo "malicious"


def vt_url_id(url: str) -> str:
    """Identificador de URL que espera VirusTotal: base64 url-safe sin '='."""
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _verdict(malicious: int, suspicious: int, harmless: int) -> Verdict:
    if malicious >= MALICIOUS_THRESHOLD:
        return "malicious"
    if malicious >= 1 or suspicious >= 1:
        return "suspicious"
    if harmless > 0:
        return "benign"
    return "unknown"


class VirusTotal(EnrichmentProvider):
    name = "virustotal"
    title = "VirusTotal"
    applies_to = frozenset({"ip", "domain", "url", "hash"})
    key_setting = "virustotal_api_key"

    def _endpoint(self, entity: EntityRecord) -> str:
        value = indicator_value(entity)
        if entity.type == "ip":
            return f"https://www.virustotal.com/api/v3/ip_addresses/{value}"
        if entity.type == "domain":
            return f"https://www.virustotal.com/api/v3/domains/{value}"
        if entity.type == "hash":
            return f"https://www.virustotal.com/api/v3/files/{value}"
        return f"https://www.virustotal.com/api/v3/urls/{vt_url_id(value)}"

    async def _run(self, indicator: EntityRecord) -> EnrichmentResult:
        response = await self._request("GET", self._endpoint(indicator),
                                       headers={"x-apikey": self.api_key()})
        if response.status_code == 404:
            return self._result(indicator, "no_data", message="VirusTotal no conoce este objeto.")
        self._check(response)
        body = self._json(response)
        attrs: dict[str, Any] = (body.get("data") or {}).get("attributes") or {}
        stats: dict[str, Any] = attrs.get("last_analysis_stats") or {}
        malicious = int(stats.get("malicious", 0))
        suspicious = int(stats.get("suspicious", 0))
        harmless = int(stats.get("harmless", 0))
        total = malicious + suspicious + harmless + int(stats.get("undetected", 0))

        labels = [f"vt:{malicious}/{total}"]
        labels += [f"etiqueta:{t}" for t in (attrs.get("tags") or [])[:10]]
        if attrs.get("reputation") is not None:
            labels.append(f"vt:reputacion={attrs['reputation']}")
        return self._result(
            indicator, "ok", verdict=_verdict(malicious, suspicious, harmless), labels=labels,
            raw=body, message=f"{malicious} de {total} motores la marcan como maliciosa.",
        )
