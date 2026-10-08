"""Shodan InternetDB: puertos, CPE, vulnerabilidades y hostnames de una IPv4. Sin clave."""

from __future__ import annotations

from aleph.core.schemas import EntityRecord

from .base import (
    REL_HAS_VULNERABILITY,
    REL_RESOLVES_TO,
    EnrichmentProvider,
    EnrichmentResult,
    indicator_ref,
    indicator_value,
    ip_version,
    new_entity,
    new_relation,
)


class ShodanInternetDB(EnrichmentProvider):
    """Respuesta documentada: {"cpes", "hostnames", "ip", "ports", "tags", "vulns"}.

    Sin datos la API responde 404 con {"detail": "No information available"}.
    Solo se consultan IPv4 (IPv6 no verificado).
    """

    name = "shodan_internetdb"
    title = "Shodan InternetDB"
    applies_to = frozenset({"ip"})

    def supports(self, entity: EntityRecord) -> bool:
        return super().supports(entity) and ip_version(entity) == 4

    async def _run(self, indicator: EntityRecord) -> EnrichmentResult:
        ip = indicator_value(indicator)
        response = await self._request("GET", f"https://internetdb.shodan.io/{ip}")
        if response.status_code == 404:
            return self._result(indicator, "no_data", message="Sin datos en Shodan InternetDB.")
        self._check(response)
        data = self._json(response)

        ref = indicator_ref(indicator)
        ports = [str(p) for p in data.get("ports", [])]
        vulns = [str(v) for v in data.get("vulns", [])]
        tags = [str(t) for t in data.get("tags", [])]
        labels = [f"puerto:{p}" for p in ports] + [f"vuln:{v}" for v in vulns] + [
            f"etiqueta:{t}" for t in tags]

        entities: list[EntityRecord] = []
        relations = []
        for vuln in vulns:
            ent = new_entity("vulnerability", vuln, {"cve_id": vuln}, confidence=0.7)
            entities.append(ent)
            relations.append(new_relation(ref, ent.ref, REL_HAS_VULNERABILITY, 0.7))
        for host in data.get("hostnames", []):
            ent = new_entity("domain", str(host).lower(), {"source": "shodan_hostname"}, 0.7)
            entities.append(ent)
            relations.append(new_relation(ent.ref, ref, REL_RESOLVES_TO, 0.7))

        return self._result(
            indicator, "ok", labels=labels, entities=entities, relations=relations, raw=data,
            message=f"{len(ports)} puertos, {len(vulns)} vulnerabilidades, "
                    f"{len(data.get('hostnames', []))} hostnames.",
        )
