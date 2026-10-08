"""Inteligencia de dominio: RDAP (RFC 9083), DNS y subdominios de crt.sh. Sin red.

El resolver DNS se reemplaza por una respuesta fija: ningún test consulta DNS real.
"""

import httpx
import pytest
from connector_helpers import NO_KEYS, client_for, json_response, no_sleep

from aleph.connectors.base import ConnectorError
from aleph.connectors.domain_intel import DomainIntelConnector

DNS_ANSWERS = {
    "A": ["93.184.216.34"],
    "AAAA": [],
    "MX": ["mx1.proveedor.example"],
    "NS": ["ns1.proveedor.example"],
    "TXT": ["v=spf1 include:_spf.proveedor.example ~all"],
}

RDAP = {
    "objectClassName": "domain",
    "handle": "2336799_DOMAIN_COM-VRSN",
    "ldhName": "EJEMPLO.COM",
    "status": ["active"],
    "events": [
        {"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2030-08-13T04:00:00Z"},
        {"eventAction": "last changed", "eventDate": "2024-01-01T00:00:00Z"},
    ],
    "nameservers": [{"objectClassName": "nameserver", "ldhName": "NS1.EJEMPLO.COM"}],
    "entities": [
        {"objectClassName": "entity", "roles": ["registrant"], "vcardArray": ["vcard", [
            ["version", {}, "text", "4.0"],
            ["fn", {}, "text", "REDACTED FOR PRIVACY"],
            ["org", {}, "text", "Organización Demo SA"],
            ["email", {}, "text", "REDACTED FOR PRIVACY"],
        ]]},
        {"objectClassName": "entity", "roles": ["registrar"], "vcardArray": ["vcard", [
            ["version", {}, "text", "4.0"],
            ["fn", {}, "text", "Registrador Demo"],
            ["org", {}, "text", "Registrador Demo LLC"],
        ]]},
        {"objectClassName": "entity", "roles": ["abuse"], "vcardArray": ["vcard", [
            ["version", {}, "text", "4.0"],
            ["email", {}, "text", "abuse@registrador.example"],
        ]]},
    ],
}

CRT = [
    {"issuer_ca_id": 1, "name_value": "www.ejemplo.com\n*.ejemplo.com\nejemplo.com",
     "entry_timestamp": "2024-01-10T10:00:00.123", "not_before": "2024-01-10T00:00:00"},
    {"issuer_ca_id": 2, "name_value": "api.ejemplo.com",
     "entry_timestamp": "2024-03-01T00:00:00", "not_before": "2024-03-01T00:00:00"},
    {"issuer_ca_id": 3, "name_value": "otro.dominio.example",
     "entry_timestamp": "2024-01-01T00:00:00", "not_before": None},
]


def _patch_dns(monkeypatch, fail: tuple[str, ...] = ()):
    async def fake_resolve(self, name, rdtype):
        if rdtype in fail:
            raise ConnectorError(f"consulta DNS {rdtype} fallida")
        return list(DNS_ANSWERS.get(rdtype, []))

    monkeypatch.setattr(DomainIntelConnector, "_resolve", fake_resolve)


def _handler(rdap_status: int = 200, crt_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "rdap.org":
            if rdap_status != 200:
                return httpx.Response(rdap_status)
            return json_response(RDAP) if request.url.path == "/domain/ejemplo.com" \
                else httpx.Response(404)
        if request.url.host == "crt.sh":
            if crt_status != 200:
                return httpx.Response(crt_status)
            return json_response(CRT)
        return httpx.Response(404)

    return handler


async def test_collects_rdap_dns_and_subdomains(monkeypatch):
    _patch_dns(monkeypatch)
    result = await DomainIntelConnector(client=client_for(_handler()), settings=NO_KEYS).collect(
        domain="https://Ejemplo.COM/ruta"
    )
    ents = {e.ref: e for e in result.entities}

    root = ents["domain:ejemplo.com"]
    assert root.props["registered_at"] == "1995-08-14T04:00:00+00:00"
    assert root.props["expires_at"] == "2030-08-13T04:00:00+00:00"
    assert root.props["status"] == ["active"]
    assert root.props["txt"] == DNS_ANSWERS["TXT"]

    assert ents["ip:93.184.216.34"].type == "ip"
    assert ents["organization:organización demo sa"].label == "Organización Demo SA"
    assert ents["organization:registrador demo llc"].label == "Registrador Demo LLC"
    assert ents["email:abuse@registrador.example"].type == "email"
    assert "email:redacted for privacy" not in ents  # los datos ocultos se descartan
    assert ents["domain:api.ejemplo.com"].props["certificates"] == 1
    assert ents["domain:www.ejemplo.com"].props["first_certificate_at"] == (
        "2024-01-10T10:00:00.123000+00:00"
    )
    assert "domain:otro.dominio.example" not in ents  # no es subdominio

    rels = {(r.type, r.src_ref, r.dst_ref) for r in result.relations}
    assert ("resolves_to", "domain:ejemplo.com", "ip:93.184.216.34") in rels
    assert ("mail_exchanger", "domain:ejemplo.com", "domain:mx1.proveedor.example") in rels
    assert ("nameserver", "domain:ejemplo.com", "domain:ns1.ejemplo.com") in rels
    assert ("nameserver", "domain:ejemplo.com", "domain:ns1.proveedor.example") in rels
    assert ("subdomain_of", "domain:api.ejemplo.com", "domain:ejemplo.com") in rels
    assert ("registered_by", "domain:ejemplo.com", "organization:organización demo sa") in rels
    assert ("registrar", "domain:ejemplo.com", "organization:registrador demo llc") in rels
    assert ("contact_email", "domain:ejemplo.com", "email:abuse@registrador.example") in rels
    assert result.warnings == []


async def test_subdomain_limit_keeps_the_first_in_sorted_order(monkeypatch):
    _patch_dns(monkeypatch)
    result = await DomainIntelConnector(client=client_for(_handler()), settings=NO_KEYS).collect(
        domain="ejemplo.com", limit=1
    )
    subs = [e.label for e in result.entities if e.props.get("source") == "crt.sh"]
    assert subs == ["api.ejemplo.com"]


async def test_missing_rdap_record_and_dns_failure_are_warnings(monkeypatch):
    _patch_dns(monkeypatch, fail=("MX",))
    result = await DomainIntelConnector(
        client=client_for(_handler(rdap_status=404)), settings=NO_KEYS
    ).collect(domain="ejemplo.com")
    assert any("RDAP: no hay registro" in w for w in result.warnings)
    assert any("consulta DNS MX fallida" in w for w in result.warnings)
    assert not any(r.type == "mail_exchanger" for r in result.relations)
    # Lo que sí respondió sigue estando.
    assert any(r.type == "resolves_to" for r in result.relations)


async def test_crtsh_outage_is_a_warning_not_a_failure(monkeypatch):
    _patch_dns(monkeypatch)
    result = await DomainIntelConnector(
        client=client_for(_handler(crt_status=503)), settings=NO_KEYS
    ).collect(domain="ejemplo.com")
    assert any("crt.sh no disponible" in w for w in result.warnings)
    assert not any(r.type == "subdomain_of" for r in result.relations)
    assert result.raw["crtsh"] is None


async def test_429_from_crtsh_over_cap_keeps_rdap_and_dns(monkeypatch):
    waits = no_sleep(monkeypatch)
    _patch_dns(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "crt.sh":
            return httpx.Response(429, headers={"Retry-After": "900"})
        return _handler()(request)

    result = await DomainIntelConnector(client=client_for(handler), settings=NO_KEYS).collect(
        domain="ejemplo.com"
    )
    assert waits == []  # espera por encima del tope: no se espera
    assert any("crt.sh no disponible" in w for w in result.warnings)
    assert any(r.type == "resolves_to" for r in result.relations)  # DNS sigue
    assert any(r.type == "registered_by" for r in result.relations)  # RDAP sigue


@pytest.mark.parametrize("bad", ["no es un dominio", "ejemplo", "a b.com"])
async def test_invalid_domains_are_rejected(bad, monkeypatch):
    _patch_dns(monkeypatch)
    with pytest.raises(ConnectorError, match="dominio inválido"):
        await DomainIntelConnector(client=client_for(_handler()), settings=NO_KEYS).collect(
            domain=bad
        )
