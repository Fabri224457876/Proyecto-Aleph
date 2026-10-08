import asyncio
from types import SimpleNamespace

import httpx

import aleph.core.config as config_module
from aleph.core.schemas import EntityRecord, RelationRecord
from aleph.cti.enrich import DEFAULT_PROVIDERS, EnrichmentProvider, enrich_indicator
from aleph.cti.enrich.base import REL_ASSOCIATED_WITH


def _ip(value="203.0.113.5"):
    return EntityRecord(type="ip", label=value, props={"value": value}, ref=f"ip:{value}")


class _Fake(EnrichmentProvider):
    applies_to = frozenset({"ip"})

    def __init__(self, name, body, verdict="unknown", labels=()):
        super().__init__(None, api_key="")
        self.name = name
        self.title = name
        self._body = body
        self._verdict = verdict
        self._labels = list(labels)

    async def _run(self, indicator):
        return await self._body(self, indicator)


async def test_proveedores_corren_en_paralelo():
    a_started, b_started = asyncio.Event(), asyncio.Event()

    async def run_a(provider, indicator):
        a_started.set()
        await asyncio.wait_for(b_started.wait(), timeout=2)  # solo termina si B arranca también
        return provider._result(indicator, "ok", labels=["a"])

    async def run_b(provider, indicator):
        b_started.set()
        await asyncio.wait_for(a_started.wait(), timeout=2)
        return provider._result(indicator, "ok", labels=["b"])

    report = await enrich_indicator(_ip(), [_Fake("a", run_a), _Fake("b", run_b)])
    assert {r.provider: r.status for r in report.results} == {"a": "ok", "b": "ok"}


async def test_fallo_de_un_proveedor_no_tumba_a_los_demas():
    async def boom(provider, indicator):
        raise RuntimeError("bug interno")

    async def fine(provider, indicator):
        return provider._result(indicator, "ok", verdict="suspicious")

    report = await enrich_indicator(_ip(), [_Fake("roto", boom), _Fake("ok", fine)])
    by_name = {r.provider: r for r in report.results}
    assert by_name["roto"].status == "error"
    assert "fallo interno (RuntimeError)" in by_name["roto"].message
    assert by_name["ok"].status == "ok"
    assert report.verdict == "suspicious"


async def test_timeout_total_por_proveedor():
    async def slow(provider, indicator):
        await asyncio.sleep(5)
        return provider._result(indicator, "ok")

    report = await enrich_indicator(_ip(), [_Fake("lento", slow)], timeout=0.05)
    (res,) = report.results
    assert res.status == "error" and "tiempo total agotado" in res.message


async def test_veredicto_mas_severo_y_entidades_sin_repetir():
    shared = EntityRecord(type="malware", label="Emotet", props={}, ref="malware:Emotet")
    rel = RelationRecord(src_ref="ip:203.0.113.5", dst_ref="malware:Emotet",
                         type=REL_ASSOCIATED_WITH)

    async def malicious(provider, indicator):
        return provider._result(indicator, "ok", verdict="malicious", entities=[shared],
                                relations=[rel])

    async def suspicious(provider, indicator):
        dup = shared.model_copy()
        return provider._result(indicator, "ok", verdict="suspicious", entities=[dup],
                                relations=[rel])

    report = await enrich_indicator(_ip(), [_Fake("uno", malicious), _Fake("dos", suspicious)])
    assert report.verdict == "malicious"
    assert [e.label for e in report.entities] == ["Emotet"]
    assert len(report.relations) == 1


async def test_sin_proveedores_aplicables_lo_informa():
    person = EntityRecord(type="person", label="Persona", props={}, ref="p")
    report = await enrich_indicator(person)
    assert report.results == []
    assert report.verdict == "unknown"
    assert "Ningún proveedor" in report.message


def test_proveedores_por_defecto_segun_tipo():
    def applicable(entity):
        return {cls.name for cls in DEFAULT_PROVIDERS
                if cls(None, api_key="").supports(entity)}

    assert applicable(_ip("8.8.8.8")) == {
        "shodan_internetdb", "urlhaus", "threatfox", "rdap", "virustotal", "otx"}
    assert applicable(_ip("2001:db8::1")) == {"urlhaus", "threatfox", "rdap", "virustotal",
                                              "otx"}
    md5 = EntityRecord(type="hash", label="d41d8cd98f00b204e9800998ecf8427e",
                       props={"algorithm": "MD5"}, ref="h")
    assert applicable(md5) == {"urlhaus", "threatfox", "malwarebazaar", "virustotal", "otx"}
    email = EntityRecord(type="email", label="a@example.com", props={}, ref="e")
    assert applicable(email) == {"hibp"}
    domain = EntityRecord(type="domain", label="example.com", props={}, ref="d")
    assert applicable(domain) == {"urlhaus", "threatfox", "crtsh", "rdap", "virustotal", "otx"}


async def test_claves_ausentes_no_generan_trafico(monkeypatch):
    monkeypatch.setattr(config_module, "get_settings", lambda: SimpleNamespace(
        virustotal_api_key="", otx_api_key="", hibp_api_key="", abusech_auth_key=""))
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await enrich_indicator(_ip("203.0.113.5"), client=client)

    by_name = {r.provider: r.status for r in report.results}
    assert by_name["virustotal"] == "unavailable"
    assert by_name["otx"] == "unavailable"
    assert by_name["urlhaus"] == "unavailable"
    assert by_name["threatfox"] == "unavailable"
    assert by_name["shodan_internetdb"] == "no_data"
    assert by_name["rdap"] == "no_data"
    assert set(hosts) == {"internetdb.shodan.io", "rdap.org"}
