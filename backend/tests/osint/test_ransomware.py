import httpx
import pytest

from aleph.osint.errors import InvalidInputError, UpstreamError
from aleph.osint.ransomware import CLAIM_CONFIDENCE, list_victims

from .helpers import json_response, mock_client

VICTIMS_AR = [
    {"victim": "Estudio Contable Ejemplo SRL", "group": "akira", "attackdate": "2026-09-01 10:00:00",
     "country": "AR", "press": [], "infostealer": "", "updates": []},
    {"victim": "Cooperativa Demo Ltda", "group": "qilin", "attackdate": "2026-09-03 08:30:00",
     "country": "AR", "press": ["https://example.com/nota-de-prensa"]},
    {"victim": "", "group": "qilin"},  # incompleto: se omite
    {"victim": "Estudio Contable Ejemplo SRL", "group": "akira"},  # duplicado: se omite
]


async def test_victims_by_country_use_lowercase_iso_path_and_build_graph():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        return json_response(VICTIMS_AR)

    async with mock_client(handler) as client:
        report = await list_victims(country="ar", client=client)
    assert seen == ["/v2/countryvictims/ar"]
    assert report.mode == "country"
    assert report.query == "país: AR"
    assert [v.victim for v in report.victims] == ["Estudio Contable Ejemplo SRL", "Cooperativa Demo Ltda"]
    assert report.skipped == 1  # el registro sin nombre
    assert report.victims[1].press == ["https://example.com/nota-de-prensa"]
    orgs = [e for e in report.entities if e.type == "organization"]
    groups = [e for e in report.entities if e.type == "malware"]
    assert len(orgs) == 2 and {g.label for g in groups} == {"akira", "qilin"}
    assert all(e.confidence == CLAIM_CONFIDENCE for e in orgs + groups)
    assert {r.type for r in report.relations} == {"targeted_by"}
    assert all("reivindicación" in o.props["fuente"] for o in orgs)


async def test_victims_by_group_escapes_the_name():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.raw_path.decode())
        return json_response([VICTIMS_AR[0]])

    async with mock_client(handler) as client:
        report = await list_victims(group="lock bit", client=client)
    assert seen == ["/v2/groupvictims/lock%20bit"]
    assert report.mode == "group"


async def test_recent_victims_default_endpoint():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        return json_response([])

    async with mock_client(handler) as client:
        report = await list_victims(client=client)
    assert seen == ["/v2/recentvictims"]
    assert report.victims == [] and report.mode == "recent"


async def test_max_items_truncates_with_warning():
    async with mock_client(lambda r: json_response(VICTIMS_AR)) as client:
        report = await list_victims(country="AR", client=client, max_items=1)
    assert len(report.victims) == 1
    assert report.truncated is True
    assert report.warnings


async def test_unexpected_payload_is_upstream_error():
    async with mock_client(lambda r: json_response({"error": "x"})) as client:
        with pytest.raises(UpstreamError):
            await list_victims(country="AR", client=client)


@pytest.mark.parametrize("country", ["ARG", "A", "1A", "AR-"])
async def test_invalid_country_codes_are_rejected_without_requests(country):
    def handler(request: httpx.Request):  # pragma: no cover - no debe ejecutarse
        raise AssertionError("no debía consultarse la red")

    async with mock_client(handler) as client:
        with pytest.raises(InvalidInputError):
            await list_victims(country=country, client=client)


async def test_country_and_group_together_are_rejected():
    async with mock_client(lambda r: json_response([])) as client:
        with pytest.raises(InvalidInputError):
            await list_victims(country="AR", group="akira", client=client)
