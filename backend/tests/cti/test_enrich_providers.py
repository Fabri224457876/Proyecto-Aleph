"""Proveedores de enriquecimiento con httpx.MockTransport: sin red.

Respuestas de ejemplo siguen la forma documentada de cada API. Los dominios e IP son
ficticios o de documentación (RFC 5737 y example.*).
"""

import base64
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

import aleph.core.config as config_module
from aleph.core.schemas import EntityRecord
from aleph.cti.enrich import (
    CrtSh,
    Hibp,
    MalwareBazaar,
    Otx,
    Rdap,
    ShodanInternetDB,
    ThreatFox,
    URLhaus,
    VirusTotal,
)
from aleph.cti.enrich.virustotal import vt_url_id


def _entity(kind, value, **props):
    return EntityRecord(type=kind, label=value, props={"value": value, **props},
                        ref=f"{kind}:{value}")


def _json(payload, status=200, headers=None):
    return httpx.Response(status, json=payload, headers=headers)


async def _run(provider_cls, entity, handler, **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await provider_cls(client, **kwargs).enrich(entity)


def _no_calls():
    calls: list[httpx.Request] = []

    def handler(request):
        calls.append(request)
        raise AssertionError(f"no debía llamarse a la red: {request.url}")

    return calls, handler


# -- Shodan InternetDB (sin clave) --------------------------------------------------------


async def test_shodan_ok_expone_puertos_cves_y_hostnames():
    def handler(request):
        assert request.url.path == "/8.8.8.8"
        return _json({"ip": "8.8.8.8", "ports": [53, 443], "hostnames": ["dns.google"],
                      "vulns": ["CVE-2021-44228"], "tags": ["cloud"], "cpes": []})

    res = await _run(ShodanInternetDB, _entity("ip", "8.8.8.8"), handler)
    assert res.status == "ok" and res.verdict == "unknown"
    assert "puerto:443" in res.labels and "vuln:CVE-2021-44228" in res.labels
    assert {e.type for e in res.entities} == {"domain", "vulnerability"}
    assert {r.type for r in res.relations} == {"resolves_to", "has_vulnerability"}


async def test_shodan_sin_datos_es_no_data():
    res = await _run(ShodanInternetDB, _entity("ip", "192.0.2.1"),
                     lambda r: _json({"detail": "No information available"}, status=404))
    assert res.status == "no_data"


async def test_shodan_no_consulta_ipv6():
    calls, handler = _no_calls()
    res = await _run(ShodanInternetDB, _entity("ip", "2001:db8::1"), handler)
    assert res.status == "unsupported" and calls == []


async def test_shodan_429_expone_retry_after():
    res = await _run(ShodanInternetDB, _entity("ip", "8.8.8.8"),
                     lambda r: httpx.Response(429, headers={"retry-after": "7"}))
    assert res.status == "rate_limited" and res.retry_after == 7.0


async def test_shodan_500_es_error_controlado():
    res = await _run(ShodanInternetDB, _entity("ip", "8.8.8.8"),
                     lambda r: httpx.Response(500))
    assert res.status == "error" and "HTTP 500" in res.message


async def test_shodan_respuesta_no_json_es_error():
    res = await _run(ShodanInternetDB, _entity("ip", "8.8.8.8"),
                     lambda r: httpx.Response(200, text="<html>mantenimiento</html>"))
    assert res.status == "error" and "JSON" in res.message


# -- abuse.ch: requieren Auth-Key ----------------------------------------------------------


async def test_abusech_sin_clave_no_llama_a_la_red():
    for cls in (URLhaus, ThreatFox, MalwareBazaar):
        calls, handler = _no_calls()
        entity = _entity("url", "http://example.com/x") if cls is not MalwareBazaar \
            else _entity("hash", "d41d8cd98f00b204e9800998ecf8427e")
        res = await _run(cls, entity, handler, api_key="")
        assert res.status == "unavailable", cls.name
        assert "ALEPH_ABUSECH_AUTH_KEY" in res.message
        assert calls == []


async def test_urlhaus_url_envia_auth_key_y_clasifica_como_malicioso():
    sha = "ab" * 32

    def handler(request):
        assert request.url.path == "/v1/url/"
        assert request.headers["Auth-Key"] == "CLAVE-PRUEBA"
        assert parse_qs(request.content.decode()) == {"url": ["http://evil.example/x.exe"]}
        return _json({"query_status": "ok", "url_status": "online", "threat": "malware_download",
                      "tags": ["Emotet"],
                      "payloads": [{"response_sha256": sha, "signature": "Emotet",
                                    "file_type": "exe"}]})

    res = await _run(URLhaus, _entity("url", "http://evil.example/x.exe"), handler,
                     api_key="CLAVE-PRUEBA")
    assert res.status == "ok" and res.verdict == "malicious"
    assert {e.type for e in res.entities} == {"hash", "malware"}
    assert {r.type for r in res.relations} == {"delivers", "associated_with"}


async def test_urlhaus_host_sin_resultados_y_con_urls():
    res = await _run(URLhaus, _entity("domain", "example.com"),
                     lambda r: _json({"query_status": "no_results"}), api_key="K")
    assert res.status == "no_data"

    res = await _run(URLhaus, _entity("domain", "evil.example"),
                     lambda r: _json({"query_status": "ok", "url_count": 3,
                                      "urls": [{"url_status": "online"},
                                               {"url_status": "offline"}]}), api_key="K")
    assert res.verdict == "malicious"
    assert "urlhaus:urls=3" in res.labels and "urlhaus:en_linea=1" in res.labels


async def test_urlhaus_payload_md5_usa_su_campo():
    seen = {}

    def handler(request):
        seen.update(parse_qs(request.content.decode()))
        return _json({"query_status": "ok", "signature": "AgentTesla", "file_type": "exe"})

    res = await _run(URLhaus, _entity("hash", "d41d8cd98f00b204e9800998ecf8427e"), handler,
                     api_key="K")
    assert seen == {"md5_hash": ["d41d8cd98f00b204e9800998ecf8427e"]}
    assert res.verdict == "malicious" and res.entities[0].label == "AgentTesla"


async def test_urlhaus_no_consulta_sha1():
    calls, handler = _no_calls()
    res = await _run(URLhaus, _entity("hash", "da39a3ee5e6b4b0d3255bfef95601890afd80709"),
                     handler, api_key="K")
    assert res.status == "unsupported" and calls == []


async def test_urlhaus_clave_rechazada_es_error():
    res = await _run(URLhaus, _entity("domain", "example.com"),
                     lambda r: httpx.Response(401, json={"error": "Unauthorized"}), api_key="X")
    assert res.status == "error" and "acceso rechazado" in res.message


async def test_threatfox_post_json_y_familia():
    def handler(request):
        body = request.read().decode()
        assert '"query":"search_ioc"' in body.replace(" ", "")
        assert '"exact_match":false' in body.replace(" ", "")  # IP: búsqueda parcial
        assert request.headers["Auth-Key"] == "K"
        return _json({"query_status": "ok", "data": [{
            "ioc": "203.0.113.5:443", "threat_type": "botnet_cc",
            "malware_printable": "Cobalt Strike", "tags": ["c2"]}]})

    res = await _run(ThreatFox, _entity("ip", "203.0.113.5"), handler, api_key="K")
    assert res.status == "ok" and res.verdict == "malicious"
    assert "threatfox:botnet_cc" in res.labels
    assert res.entities[0].label == "Cobalt Strike"


async def test_threatfox_sin_resultados_y_estado_inesperado():
    res = await _run(ThreatFox, _entity("domain", "example.com"),
                     lambda r: _json({"query_status": "no_result"}), api_key="K")
    assert res.status == "no_data"
    res = await _run(ThreatFox, _entity("domain", "example.com"),
                     lambda r: _json({"query_status": "illegal_search_term"}), api_key="K")
    assert res.status == "error"


async def test_malwarebazaar_hash_no_encontrado_y_formatos_de_data():
    res = await _run(MalwareBazaar, _entity("hash", "d41d8cd98f00b204e9800998ecf8427e"),
                     lambda r: _json({"query_status": "hash_not_found"}), api_key="K")
    assert res.status == "no_data"

    def as_list(request):
        assert parse_qs(request.content.decode()) == {
            "query": ["get_info"], "hash": ["d41d8cd98f00b204e9800998ecf8427e"]}
        return _json({"query_status": "ok", "data": [
            {"signature": "Emotet", "file_type": "exe", "tags": ["doc"]}]})

    res = await _run(MalwareBazaar, _entity("hash", "d41d8cd98f00b204e9800998ecf8427e"),
                     as_list, api_key="K")
    assert res.verdict == "malicious" and res.entities[0].label == "Emotet"

    res = await _run(MalwareBazaar, _entity("hash", "d41d8cd98f00b204e9800998ecf8427e"),
                     lambda r: _json({"query_status": "ok",
                                      "data": {"signature": "TrickBot"}}), api_key="K")
    assert res.entities[0].label == "TrickBot"


# -- crt.sh (sin clave) --------------------------------------------------------------------


async def test_crtsh_subdominios_wildcards_y_consulta():
    rows = [
        {"name_value": "*.example.com\nwww.example.com"},
        {"name_value": "api.example.com\nexample.com"},
        {"name_value": "otro.dominio.org"},
    ]

    def handler(request):
        assert request.url.params["q"] == "%.example.com"
        assert request.url.params["output"] == "json"
        return _json(rows)

    res = await _run(CrtSh, _entity("domain", "example.com"), handler)
    assert res.status == "ok"
    assert {e.label for e in res.entities} == {"www.example.com", "api.example.com"}
    assert {r.type for r in res.relations} == {"subdomain_of"}
    assert "crtsh:certificados=3" in res.labels and "crtsh:subdominios=2" in res.labels


async def test_crtsh_502_no_bloquea_y_lista_vacia():
    res = await _run(CrtSh, _entity("domain", "example.com"),
                     lambda r: httpx.Response(502, text="Bad Gateway"))
    assert res.status == "error" and "HTTP 502" in res.message
    res = await _run(CrtSh, _entity("domain", "example.com"), lambda r: _json([]))
    assert res.status == "no_data"


async def test_crtsh_respuesta_html_es_error():
    res = await _run(CrtSh, _entity("domain", "example.com"),
                     lambda r: httpx.Response(200, text="<html>error</html>"))
    assert res.status == "error"


# -- RDAP (sin clave) ----------------------------------------------------------------------


def _vcard(name):
    return ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", name]]]


async def test_rdap_dominio_registrar_eventos_y_nameservers():
    payload = {
        "objectClassName": "domain", "ldhName": "EXAMPLE.COM", "status": ["active"],
        "events": [{"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"}],
        "nameservers": [{"ldhName": "A.IANA-SERVERS.NET"}],
        "entities": [{"roles": ["registrar"], "vcardArray": _vcard("Registrar Ejemplo SA")}],
    }

    def handler(request):
        assert request.url.path == "/domain/example.com"
        return _json(payload)

    res = await _run(Rdap, _entity("domain", "example.com"), handler)
    assert res.status == "ok"
    assert "rdap:estado=active" in res.labels
    assert "rdap:registro=1995-08-14" in res.labels
    assert "rdap:ns=a.iana-servers.net" in res.labels
    (org,) = res.entities
    assert (org.type, org.label) == ("organization", "Registrar Ejemplo SA")
    assert [r.type for r in res.relations] == ["registered_by"]


async def test_rdap_ip_red_pais_y_registrante_y_404():
    payload = {"name": "TEST-NET-1", "country": "AR",
               "entities": [{"roles": ["registrant"], "vcardArray": _vcard("Org Ejemplo")}]}

    def handler(request):
        assert request.url.path == "/ip/192.0.2.10"
        return _json(payload)

    res = await _run(Rdap, _entity("ip", "192.0.2.10"), handler)
    assert "rdap:red=TEST-NET-1" in res.labels and "rdap:pais=AR" in res.labels
    assert res.entities[0].label == "Org Ejemplo"

    res = await _run(Rdap, _entity("ip", "192.0.2.10"), lambda r: httpx.Response(404))
    assert res.status == "no_data"


# -- VirusTotal (con clave) ----------------------------------------------------------------


async def test_virustotal_ip_maliciosa_con_header_apikey():
    def handler(request):
        assert request.url.path == "/api/v3/ip_addresses/203.0.113.9"
        assert request.headers["x-apikey"] == "VT-PRUEBA"
        return _json({"data": {"attributes": {
            "last_analysis_stats": {"malicious": 5, "suspicious": 0, "undetected": 60,
                                    "harmless": 10, "timeout": 0},
            "tags": ["botnet"], "reputation": -20}}})

    res = await _run(VirusTotal, _entity("ip", "203.0.113.9"), handler, api_key="VT-PRUEBA")
    assert res.verdict == "malicious"
    assert "vt:5/75" in res.labels and "etiqueta:botnet" in res.labels


async def test_virustotal_umbrales_sospechoso_y_benigno():
    def stats(malicious, suspicious, harmless):
        return lambda r: _json({"data": {"attributes": {"last_analysis_stats": {
            "malicious": malicious, "suspicious": suspicious, "harmless": harmless,
            "undetected": 10}}}})

    entity = _entity("domain", "example.com")
    assert (await _run(VirusTotal, entity, stats(1, 0, 0), api_key="K")).verdict == "suspicious"
    assert (await _run(VirusTotal, entity, stats(0, 0, 5), api_key="K")).verdict == "benign"


async def test_virustotal_url_usa_id_base64_sin_relleno():
    url = "https://evil.example/login?x=1"
    expected = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    assert vt_url_id(url) == expected and "=" not in expected

    def handler(request):
        assert request.url.path == f"/api/v3/urls/{expected}"
        return httpx.Response(404)

    res = await _run(VirusTotal, _entity("url", url), handler, api_key="K")
    assert res.status == "no_data"


async def test_virustotal_errores_429_y_sin_clave():
    res = await _run(VirusTotal, _entity("hash", "d41d8cd98f00b204e9800998ecf8427e"),
                     lambda r: httpx.Response(429), api_key="K")
    assert res.status == "rate_limited" and res.retry_after is None
    calls, handler = _no_calls()
    res = await _run(VirusTotal, _entity("ip", "8.8.8.8"), handler, api_key="")
    assert res.status == "unavailable" and calls == []


# -- OTX (con clave) -----------------------------------------------------------------------


async def test_otx_dominio_pulsos_familias_y_header():
    payload = {"pulse_info": {"count": 2, "pulses": [
        {"name": "Campaña X", "adversary": "APT-Ejemplo",
         "malware_families": ["Emotet", {"display_name": "TrickBot"}]},
        {"name": "Otra", "adversary": "", "malware_families": ["Emotet"]},
    ]}, "reputation": 0}

    def handler(request):
        assert request.url.path == "/api/v1/indicators/domain/example.com/general"
        assert request.headers["X-OTX-API-KEY"] == "OTX-PRUEBA"
        return _json(payload)

    res = await _run(Otx, _entity("domain", "example.com"), handler, api_key="OTX-PRUEBA")
    assert res.verdict == "suspicious"
    assert "otx:pulsos=2" in res.labels and "otx:adversario=APT-Ejemplo" in res.labels
    assert {e.label for e in res.entities} == {"Emotet", "TrickBot"}
    assert {r.type for r in res.relations} == {"associated_with"}


async def test_otx_ipv6_hash_y_no_encontrado():
    def handler(request):
        assert request.url.path == "/api/v1/indicators/IPv6/2001:db8::1/general"
        return _json({"pulse_info": {"count": 0, "pulses": []}})

    res = await _run(Otx, _entity("ip", "2001:db8::1"), handler, api_key="K")
    assert res.status == "ok" and res.verdict == "unknown"

    def hash_handler(request):
        assert "/indicators/file/" in request.url.path
        return httpx.Response(404)

    res = await _run(Otx, _entity("hash", "d41d8cd98f00b204e9800998ecf8427e"), hash_handler,
                     api_key="K")
    assert res.status == "no_data"


# -- HIBP (con clave) ----------------------------------------------------------------------


async def test_hibp_brechas_headers_privacidad_y_eventos():
    breaches = [{"Name": "Adobe", "Title": "Adobe", "Domain": "adobe.com",
                 "BreachDate": "2013-10-04", "DataClasses": ["Email addresses", "Passwords"]}]

    def handler(request):
        assert request.url.path == "/api/v3/breachedaccount/usuario@example.com"
        assert b"%40" in request.url.raw_path  # el correo va escapado en la ruta
        assert request.headers["hibp-api-key"] == "HIBP-PRUEBA"
        assert request.headers["user-agent"].startswith("Aleph-CTI")
        assert request.url.params["truncateResponse"] == "false"
        return _json(breaches)

    res = await _run(Hibp, _entity("email", "usuario@example.com"), handler,
                     api_key="HIBP-PRUEBA")
    assert res.status == "ok"
    (event,) = res.entities
    assert event.type == "event" and event.label == "Brecha: Adobe"
    assert [r.type for r in res.relations] == ["exposed_in"]
    assert "hibp:brechas=1" in res.labels
    assert not any("usuario" in label for label in res.labels)  # sin el correo en etiquetas


async def test_hibp_404_401_y_429():
    entity = _entity("email", "nadie@example.com")
    assert (await _run(Hibp, entity, lambda r: httpx.Response(404), api_key="K")).status == \
        "no_data"
    res = await _run(Hibp, entity, lambda r: httpx.Response(401), api_key="K")
    assert res.status == "error" and "acceso rechazado" in res.message
    res = await _run(Hibp, entity,
                     lambda r: httpx.Response(429, headers={"retry-after": "2"}), api_key="K")
    assert res.status == "rate_limited" and res.retry_after == 2.0


# -- Transversal ---------------------------------------------------------------------------


_TIMEOUT_CASES = [
    (ShodanInternetDB, "ip", "8.8.8.8", None),
    (URLhaus, "domain", "example.com", "K"),
    (ThreatFox, "domain", "example.com", "K"),
    (MalwareBazaar, "hash", "d41d8cd98f00b204e9800998ecf8427e", "K"),
    (CrtSh, "domain", "example.com", None),
    (Rdap, "domain", "example.com", None),
    (VirusTotal, "domain", "example.com", "K"),
    (Otx, "domain", "example.com", "K"),
    (Hibp, "email", "usuario@example.com", "K"),
]


@pytest.mark.parametrize(("cls", "kind", "value", "key"), _TIMEOUT_CASES,
                         ids=[c[0].name for c in _TIMEOUT_CASES])
async def test_timeout_y_respuesta_no_json_nunca_escapan(cls, kind, value, key):
    def timeout(request):
        raise httpx.ReadTimeout("servidor lento", request=request)

    kwargs = {"api_key": key} if key else {}
    res = await _run(cls, _entity(kind, value), timeout, **kwargs)
    assert res.status == "error" and "tiempo de espera" in res.message

    res = await _run(cls, _entity(kind, value),
                     lambda r: httpx.Response(200, text="no es json"), **kwargs)
    assert res.status == "error"  # texto plano no es un resultado válido, ni lanza excepción


def test_user_agent_es_ascii_porque_httpx_rechaza_otro_caracter():
    from aleph.cti.enrich.base import USER_AGENT

    USER_AGENT.encode("ascii")  # si no, httpx falla en cada consulta


def test_hash_sin_algoritmo_declarado_se_infiere_por_longitud():
    urlhaus = URLhaus()
    md5 = _entity("hash", "d41d8cd98f00b204e9800998ecf8427e")
    sha1 = _entity("hash", "da39a3ee5e6b4b0d3255bfef95601890afd80709")
    assert urlhaus.supports(md5) is True
    assert urlhaus.supports(sha1) is False  # URLhaus no consulta SHA-1


def test_settings_faltantes_quedan_como_sin_clave(monkeypatch):
    monkeypatch.setattr(config_module, "get_settings",
                        lambda: SimpleNamespace(virustotal_api_key="K-DESDE-SETTINGS"))
    assert VirusTotal().api_key() == "K-DESDE-SETTINGS"
    assert URLhaus().api_key() == ""  # setting todavía no definido en core/config.py
