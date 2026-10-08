"""Have I Been Pwned (API v3): brechas en las que aparece un correo. Con clave.

Verificado en la documentación: GET /api/v3/breachedaccount/{correo} con header `hibp-api-key`
y un user-agent identificable (sin él responde 403). 404 = sin brechas; 401 = clave inválida;
429 trae `retry-after`.

Privacidad: el correo viaja a un tercero (HIBP) como parte de la ruta, porque así lo exige la
API. Usarlo requiere base legal y propósito del caso (Ley 25.326); el orquestador de la
plataforma debe exigirlo antes de llamar a este proveedor.
"""

from __future__ import annotations

from urllib.parse import quote

from aleph.core.schemas import EntityRecord

from .base import (
    REL_EXPOSED_IN,
    EnrichmentProvider,
    EnrichmentResult,
    indicator_ref,
    indicator_value,
    new_entity,
    new_relation,
)

API_URL = "https://haveibeenpwned.com/api/v3/breachedaccount/{account}"


class Hibp(EnrichmentProvider):
    name = "hibp"
    title = "Have I Been Pwned"
    applies_to = frozenset({"email"})
    key_setting = "hibp_api_key"

    async def _run(self, indicator: EntityRecord) -> EnrichmentResult:
        account = quote(indicator_value(indicator), safe="")
        response = await self._request(
            "GET", API_URL.format(account=account),
            params={"truncateResponse": "false"},
            headers={"hibp-api-key": self.api_key()},
        )
        if response.status_code == 404:
            return self._result(indicator, "no_data",
                                message="El correo no figura en brechas conocidas.")
        self._check(response)
        breaches = self._json(response)
        if not isinstance(breaches, list):
            return self._result(indicator, "error", message="HIBP devolvió un formato inesperado.")

        ref = indicator_ref(indicator)
        entities: list[EntityRecord] = []
        relations = []
        data_classes: set[str] = set()
        for breach in breaches:
            if not isinstance(breach, dict):
                continue
            title = str(breach.get("Title") or breach.get("Name") or "brecha").strip()
            classes = [str(c) for c in breach.get("DataClasses") or []]
            data_classes.update(classes)
            ent = new_entity("event", f"Brecha: {title}", {
                "breach": breach.get("Name"),
                "domain": breach.get("Domain"),
                "breach_date": breach.get("BreachDate"),
                "data_classes": classes,
            }, confidence=0.9)
            entities.append(ent)
            relations.append(new_relation(ref, ent.ref, REL_EXPOSED_IN, 0.9))

        labels = [f"hibp:brechas={len(breaches)}"]
        labels += [f"hibp:dato={c}" for c in sorted(data_classes)[:10]]
        return self._result(
            indicator, "ok", labels=labels, entities=entities, relations=relations,
            raw=breaches, message=f"El correo aparece en {len(breaches)} brechas.",
        )
