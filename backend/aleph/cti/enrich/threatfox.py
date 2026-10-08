"""abuse.ch ThreatFox: IOCs de malware conocidos (IP:puerto, dominio, URL, hash). Requiere Auth-Key.

Verificado: header `Auth-Key`; POST JSON con `query` = "search_ioc", `search_term` y
`exact_match`. El formato de "sin resultados" no está documentado; se trata cualquier
`query_status` distinto de ok/no_result(s) como error.
"""

from __future__ import annotations

from aleph.core.schemas import EntityRecord

from .base import (
    REL_ASSOCIATED_WITH,
    EnrichmentProvider,
    EnrichmentResult,
    indicator_ref,
    indicator_value,
    new_entity,
    new_relation,
)

API_URL = "https://threatfox-api.abuse.ch/api/v1/"


class ThreatFox(EnrichmentProvider):
    name = "threatfox"
    title = "abuse.ch ThreatFox"
    applies_to = frozenset({"ip", "domain", "url", "hash"})
    key_setting = "abusech_auth_key"

    async def _run(self, indicator: EntityRecord) -> EnrichmentResult:
        value = indicator_value(indicator)
        # Las IP suelen guardarse como "ip:puerto": búsqueda parcial para ellas.
        payload = {"query": "search_ioc", "search_term": value,
                   "exact_match": indicator.type != "ip"}
        response = await self._request("POST", API_URL,
                                       headers={"Auth-Key": self.api_key()}, json=payload)
        self._check(response)
        body = self._json(response)
        status = body.get("query_status")
        data = body.get("data") or []
        if status not in ("ok", "no_result", "no_results"):
            return self._result(indicator, "error", raw=body,
                                message=f"ThreatFox devolvió query_status={status}.")
        if not isinstance(data, list) or not data:
            return self._result(indicator, "no_data", raw=body,
                                message="Sin resultados en ThreatFox.")

        ref = indicator_ref(indicator)
        labels: list[str] = []
        entities: list[EntityRecord] = []
        relations = []
        seen_families: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            threat = item.get("threat_type")
            if threat:
                labels.append(f"threatfox:{threat}")
            labels += [f"etiqueta:{t}" for t in item.get("tags") or []]
            family = str(item.get("malware_printable") or item.get("malware") or "").strip()
            if family and family.lower() not in seen_families:
                seen_families.add(family.lower())
                ent = _family(family)
                entities.append(ent)
                relations.append(new_relation(ref, ent.ref, REL_ASSOCIATED_WITH, 0.8))
        return self._result(
            indicator, "ok", verdict="malicious", labels=labels, entities=entities,
            relations=relations, raw=body,
            message=f"{len(data)} IOC coincidentes en ThreatFox.",
        )


def _family(name: str) -> EntityRecord:
    return new_entity("malware", name, {"source": "threatfox"}, confidence=0.8)
