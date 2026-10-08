"""crt.sh: subdominios que aparecen en certificados públicos (Certificate Transparency). Sin clave.

La consulta usa `q=%.dominio&output=json`. El servicio a veces responde 502/503: en ese caso
el resultado es `error` y no bloquea el resto del enriquecimiento.
"""

from __future__ import annotations

from aleph.core.schemas import EntityRecord

from .base import (
    REL_SUBDOMAIN_OF,
    EnrichmentProvider,
    EnrichmentResult,
    indicator_ref,
    indicator_value,
    new_entity,
    new_relation,
)

MAX_SUBDOMAINS = 100  # tope para no inflar el grafo con un dominio muy grande


class CrtSh(EnrichmentProvider):
    name = "crtsh"
    title = "crt.sh"
    applies_to = frozenset({"domain"})

    async def _run(self, indicator: EntityRecord) -> EnrichmentResult:
        domain = indicator_value(indicator).lower().strip(".")
        response = await self._request("GET", "https://crt.sh/",
                                       params={"q": f"%.{domain}", "output": "json"})
        if response.status_code == 404:
            return self._result(indicator, "no_data", message="crt.sh no tiene certificados.")
        self._check(response)
        rows = self._json(response)
        if not isinstance(rows, list):
            return self._result(indicator, "error",
                                message="crt.sh devolvió un formato inesperado.")
        if not rows:
            return self._result(indicator, "no_data", raw=rows,
                                message="crt.sh no tiene certificados para este dominio.")

        names: set[str] = set()
        for row in rows:
            for raw_name in str(row.get("name_value", "")).split("\n"):
                name = raw_name.strip().lower()
                name = name.removeprefix("*.")
                if name and (name == domain or name.endswith("." + domain)):
                    names.add(name)
        subdomains = sorted(n for n in names if n != domain)
        ref = indicator_ref(indicator)
        entities: list[EntityRecord] = []
        relations = []
        for sub in subdomains[:MAX_SUBDOMAINS]:
            ent = new_entity("domain", sub, {"source": "crtsh"}, confidence=0.9)
            entities.append(ent)
            relations.append(new_relation(ent.ref, ref, REL_SUBDOMAIN_OF, 0.9))
        truncated = " (lista truncada)" if len(subdomains) > MAX_SUBDOMAINS else ""
        return self._result(
            indicator, "ok", labels=[f"crtsh:certificados={len(rows)}",
                                     f"crtsh:subdominios={len(subdomains)}"],
            entities=entities, relations=relations, raw=rows,
            message=f"{len(subdomains)} subdominios en certificados públicos{truncated}.",
        )
