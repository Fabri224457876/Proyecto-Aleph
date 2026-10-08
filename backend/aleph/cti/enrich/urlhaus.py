"""abuse.ch URLhaus: URLs y hosts que distribuyen malware, y payloads. Requiere Auth-Key.

Verificado en la documentación de urlhaus-api.abuse.ch: header `Auth-Key`, POST con
`url`, `host` o `md5_hash`/`sha256_hash`. `query_status`: ok, no_results, ...
"""

from __future__ import annotations

from typing import Any

from aleph.core.schemas import EntityRecord

from .base import (
    REL_ASSOCIATED_WITH,
    REL_DELIVERS,
    EnrichmentProvider,
    EnrichmentResult,
    hash_algorithm,
    indicator_ref,
    indicator_value,
    new_entity,
    new_relation,
)

BASE_URL = "https://urlhaus-api.abuse.ch"


class URLhaus(EnrichmentProvider):
    name = "urlhaus"
    title = "abuse.ch URLhaus"
    applies_to = frozenset({"url", "domain", "ip", "hash"})
    key_setting = "abusech_auth_key"

    def supports(self, entity: EntityRecord) -> bool:
        if entity.type == "hash":
            return hash_algorithm(entity) in ("MD5", "SHA-256")  # no consulta SHA-1
        return super().supports(entity)

    async def _run(self, indicator: EntityRecord) -> EnrichmentResult:
        value = indicator_value(indicator)
        if indicator.type == "url":
            path, form = "/v1/url/", {"url": value}
        elif indicator.type in ("domain", "ip"):
            path, form = "/v1/host/", {"host": value}
        else:
            field = "sha256_hash" if hash_algorithm(indicator) == "SHA-256" else "md5_hash"
            path, form = "/v1/payload/", {field: value}

        response = await self._request("POST", BASE_URL + path,
                                       headers={"Auth-Key": self.api_key()}, data=form)
        self._check(response)
        body = self._json(response)
        status = body.get("query_status")
        if status == "no_results":
            return self._result(indicator, "no_data", raw=body,
                                message="Sin resultados en URLhaus.")
        if status != "ok":
            return self._result(indicator, "error", raw=body,
                                message=f"URLhaus devolvió query_status={status}.")

        if indicator.type == "url":
            return self._from_url(indicator, body)
        if indicator.type in ("domain", "ip"):
            return self._from_host(indicator, body)
        return self._from_payload(indicator, body)

    def _from_url(self, indicator: EntityRecord, body: dict[str, Any]) -> EnrichmentResult:
        ref = indicator_ref(indicator)
        labels = [f"urlhaus:{body.get('url_status', 'desconocido')}"]
        if body.get("threat"):
            labels.append(f"urlhaus:{body['threat']}")
        labels += [f"etiqueta:{t}" for t in body.get("tags") or []]
        entities: list[EntityRecord] = []
        relations = []
        for payload in body.get("payloads") or []:
            sha256 = payload.get("response_sha256")
            if sha256:
                ent = new_entity("hash", sha256.lower(),
                                 {"algorithm": "SHA-256", "hashes": {"SHA-256": sha256.lower()}},
                                 confidence=0.8)
                entities.append(ent)
                relations.append(new_relation(ref, ent.ref, REL_DELIVERS, 0.8))
            if payload.get("signature"):
                family = new_entity("malware", str(payload["signature"]), confidence=0.6)
                entities.append(family)
                if sha256:
                    relations.append(new_relation(ent.ref, family.ref, REL_ASSOCIATED_WITH, 0.6))
        return self._result(
            indicator, "ok", verdict="malicious", labels=labels, entities=entities,
            relations=relations, raw=body,
            message="URL listada como distribución de malware en URLhaus.",
        )

    def _from_host(self, indicator: EntityRecord, body: dict[str, Any]) -> EnrichmentResult:
        urls = body.get("urls") or []
        count = int(body.get("url_count") or len(urls))
        online = sum(1 for u in urls if u.get("url_status") == "online")
        labels = [f"urlhaus:urls={count}", f"urlhaus:en_linea={online}"]
        return self._result(
            indicator, "ok", verdict="malicious" if count > 0 else "unknown", labels=labels,
            raw=body, message=f"{count} URLs de distribución de malware en este host.",
        )

    def _from_payload(self, indicator: EntityRecord, body: dict[str, Any]) -> EnrichmentResult:
        ref = indicator_ref(indicator)
        labels = [f"urlhaus:tipo={body.get('file_type', 'desconocido')}"]
        entities: list[EntityRecord] = []
        relations = []
        if body.get("signature"):
            family = new_entity("malware", str(body["signature"]), confidence=0.7)
            entities.append(family)
            relations.append(new_relation(ref, family.ref, REL_ASSOCIATED_WITH, 0.7))
            labels.append(f"familia:{body['signature']}")
        return self._result(indicator, "ok", verdict="malicious", labels=labels,
                            entities=entities, relations=relations, raw=body,
                            message="Payload listado en URLhaus.")
