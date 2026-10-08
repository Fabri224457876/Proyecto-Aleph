"""RDAP (vía rdap.org): registrante, registrar, estados y fechas de un dominio o IP. Sin clave.

rdap.org redirige al servidor RDAP que corresponde; por eso se siguen redirecciones.
"""

from __future__ import annotations

from typing import Any

from aleph.core.schemas import EntityRecord

from .base import (
    REL_REGISTERED_BY,
    EnrichmentProvider,
    EnrichmentResult,
    indicator_ref,
    indicator_value,
    new_entity,
    new_relation,
)


def _vcard_name(entity: dict[str, Any]) -> str:
    """Nombre visible ('fn') de una entidad RDAP, leído de su vcardArray."""
    vcard = entity.get("vcardArray")
    if isinstance(vcard, list) and len(vcard) == 2 and isinstance(vcard[1], list):
        for prop in vcard[1]:
            if isinstance(prop, list) and len(prop) >= 4 and prop[0] == "fn":
                return str(prop[3]).strip()
    return ""


def _with_role(data: dict[str, Any], role: str) -> dict[str, Any] | None:
    for entity in data.get("entities") or []:
        if isinstance(entity, dict) and role in (entity.get("roles") or []):
            return entity
    return None


class Rdap(EnrichmentProvider):
    name = "rdap"
    title = "RDAP"
    applies_to = frozenset({"domain", "ip"})

    async def _run(self, indicator: EntityRecord) -> EnrichmentResult:
        value = indicator_value(indicator)
        kind = "domain" if indicator.type == "domain" else "ip"
        response = await self._request("GET", f"https://rdap.org/{kind}/{value}",
                                       follow_redirects=True)
        if response.status_code == 404:
            return self._result(indicator, "no_data", message="Sin registro RDAP.")
        self._check(response)
        data = self._json(response)
        if not isinstance(data, dict):
            return self._result(indicator, "error", message="RDAP devolvió un formato inesperado.")

        ref = indicator_ref(indicator)
        labels: list[str] = []
        entities: list[EntityRecord] = []
        relations = []
        if kind == "domain":
            labels += [f"rdap:estado={s}" for s in data.get("status") or []]
            for ns in (data.get("nameservers") or [])[:4]:
                if isinstance(ns, dict) and ns.get("ldhName"):
                    labels.append(f"rdap:ns={str(ns['ldhName']).lower()}")
            for event in data.get("events") or []:
                if event.get("eventAction") == "registration" and event.get("eventDate"):
                    labels.append(f"rdap:registro={event['eventDate'][:10]}")
            registrar = _with_role(data, "registrar")
            name = _vcard_name(registrar) if registrar else ""
            if name:
                org = new_entity("organization", name, {"source": "rdap", "role": "registrar"},
                                 confidence=0.8)
                entities.append(org)
                relations.append(new_relation(ref, org.ref, REL_REGISTERED_BY, 0.8))
        else:
            if data.get("name"):
                labels.append(f"rdap:red={data['name']}")
            if data.get("country"):
                labels.append(f"rdap:pais={data['country']}")
            registrant = _with_role(data, "registrant")
            name = _vcard_name(registrant) if registrant else ""
            if name:
                org = new_entity("organization", name, {"source": "rdap", "role": "registrant"},
                                 confidence=0.7)
                entities.append(org)
                relations.append(new_relation(ref, org.ref, REL_REGISTERED_BY, 0.7))

        return self._result(
            indicator, "ok", labels=labels, entities=entities, relations=relations, raw=data,
            message="Registro RDAP obtenido.",
        )
