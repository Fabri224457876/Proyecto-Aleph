import httpx
import pytest

from aleph.osint.asn import announced_prefixes, lookup_ip, parse_asn
from aleph.osint.errors import InvalidInputError, UpstreamError

from .helpers import json_response, mock_client

NETWORK_INFO = {"status": "ok", "data": {"asns": ["3333"], "prefix": "193.0.0.0/21"}}
PREFIX_OVERVIEW = {"status": "ok", "data": {
    "resource": "193.0.0.0/21", "announced": "True",
    "asns": [{"asn": 3333, "holder": "RIPE-NCC-AS Reseaux IP Europeens Network Coordination Centre (RIPE NCC)"}],
    "block": {"resource": "193.0.0.0/8", "desc": "RIPE NCC", "name": "IANA"},
}}
RIR_COUNTRY = {"status": "ok", "data": {"located_resources": [{"resource": "193.0.0.0/21", "location": "NL"}]}}
ANNOUNCED = {"status": "ok", "data": {"prefixes": [
    {"prefix": "193.0.0.0/21", "timelines": []},
    {"prefix": "2001:db8::/32", "timelines": []},
]}}
AS_OVERVIEW = {"status": "ok", "data": {"resource": "AS3333", "holder": "RIPE-NCC-AS Test",
                                       "announced": True}}


def _router(routes):
    def handler(request: httpx.Request):
        path = request.url.path
        for suffix, payload in routes.items():
            if path.endswith(suffix):
                return json_response(payload)
        return json_response({"status": "error"}, status=404)

    return handler


async def test_ip_lookup_gives_asn_prefix_holder_and_registry_country():
    routes = {
        "/network-info/data.json": NETWORK_INFO,
        "/prefix-overview/data.json": PREFIX_OVERVIEW,
        "/rir-stats-country/data.json": RIR_COUNTRY,
    }
    async with mock_client(_router(routes)) as client:
        result = await lookup_ip("193.0.0.10", client=client)
    assert result.routed is True
    assert result.prefix == "193.0.0.0/21"
    assert result.announced is True
    assert [a.asn for a in result.asns] == [3333]
    assert result.asns[0].holder.startswith("RIPE-NCC-AS")
    assert result.country == "NL"
    types = {r.type for r in result.relations}
    assert types == {"announced_by", "registered_in"}
    location = next(e for e in result.entities if e.type == "location")
    assert location.label == "NL"
    assert "no geolocalización" in location.props["tipo"]


async def test_non_public_ip_is_not_queried():
    def handler(request: httpx.Request):  # pragma: no cover - no debe ejecutarse
        raise AssertionError("una IP privada no debe consultarse")

    async with mock_client(handler) as client:
        result = await lookup_ip("192.168.1.1", client=client)
    assert result.routed is False
    assert result.asns == []
    assert result.warnings


async def test_unrouted_public_ip_reports_warning_and_skips_prefix_overview():
    paths = []

    def handler(request: httpx.Request):
        paths.append(request.url.path)
        if request.url.path.endswith("/network-info/data.json"):
            return json_response({"status": "ok", "data": {"asns": [], "prefix": ""}})
        return json_response(RIR_COUNTRY)

    # 1.1.1.1 es pública; el mock la presenta como no anunciada. Los rangos de documentación
    # (198.51.100.0/24, etc.) no son globales y se rechazan antes de consultar.
    async with mock_client(handler) as client:
        result = await lookup_ip("1.1.1.1", client=client)
    assert result.routed is False
    assert not any("prefix-overview" in p for p in paths)
    assert any("no aparece anunciada" in w for w in result.warnings)


async def test_country_failure_is_a_warning_not_an_error():
    routes = {
        "/network-info/data.json": NETWORK_INFO,
        "/prefix-overview/data.json": PREFIX_OVERVIEW,
    }

    def handler(request: httpx.Request):
        if request.url.path.endswith("/rir-stats-country/data.json"):
            return json_response({"status": "error"}, status=500)
        return _router(routes)(request)

    async with mock_client(handler) as client:
        result = await lookup_ip("193.0.0.10", client=client)
    assert result.country == ""
    assert any("rir-stats-country no disponible" in w for w in result.warnings)
    assert result.prefix == "193.0.0.0/21"


async def test_announced_prefixes_for_asn_with_holder():
    routes = {"/announced-prefixes/data.json": ANNOUNCED, "/as-overview/data.json": AS_OVERVIEW}
    async with mock_client(_router(routes)) as client:
        result = await announced_prefixes("AS3333", client=client)
    assert result.asn == 3333
    assert result.holder == "RIPE-NCC-AS Test"
    assert result.prefixes == ["193.0.0.0/21", "2001:db8::/32"]
    assert (result.ipv4_count, result.ipv6_count) == (1, 1)
    assert result.entities[0].type == "organization"


async def test_ripestat_error_status_is_upstream_error():
    def handler(request: httpx.Request):
        return json_response({"status": "error", "data": {}})

    async with mock_client(handler) as client:
        with pytest.raises(UpstreamError):
            await announced_prefixes(3333, client=client)


@pytest.mark.parametrize("value", ["AS0", "abc", "AS4294967296", "3.5", ""])
def test_invalid_asn_values_are_rejected(value):
    with pytest.raises(InvalidInputError):
        parse_asn(value)


def test_asn_forms_are_accepted():
    assert parse_asn("3333") == 3333
    assert parse_asn("AS3333") == 3333
    assert parse_asn("as3333") == 3333
    assert parse_asn(3333) == 3333


async def test_invalid_ip_is_rejected():
    with pytest.raises(InvalidInputError):
        await lookup_ip("no-es-una-ip")
