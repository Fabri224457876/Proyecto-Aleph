from datetime import date

import httpx
import pytest

from aleph.osint.errors import InvalidInputError, RateLimitedError
from aleph.osint.vulns import KEV_URL, NVD_URL, lookup_cve, normalize_cve, parse_kev

from .helpers import json_response, mock_client, text_response

NVD_LOG4J = {"resultsPerPage": 1, "startIndex": 0, "totalResults": 1, "format": "NVD_CVE", "version": "2.0",
             "vulnerabilities": [{"cve": {
                 "id": "CVE-2021-44228", "sourceIdentifier": "security@apache.org",
                 "published": "2021-12-10T10:15:09.143", "lastModified": "2024-08-02T12:00:00.000",
                 "vulnStatus": "Analyzed",
                 "descriptions": [{"lang": "en", "value": "Apache Log4j2 JNDI features ... (EN)"},
                                  {"lang": "es", "value": "Log4j2 permite ejecución remota (ES)"}],
                 "metrics": {"cvssMetricV31": [
                     {"source": "nvd@nist.gov", "type": "Secondary", "cvssData": {
                         "version": "3.1", "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                         "baseScore": 9.8, "baseSeverity": "CRITICAL"}},
                     {"source": "nvd@nist.gov", "type": "Primary", "cvssData": {
                         "version": "3.1", "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H",
                         "baseScore": 10.0, "baseSeverity": "CRITICAL"}}]},
                 "weaknesses": [{"source": "nvd@nist.gov", "type": "Primary",
                                 "description": [{"lang": "en", "value": "CWE-917"}]}],
             }}]}

KEV_FIXTURE = {
    "title": "CISA Catalog of Known Exploited Vulnerabilities",
    "catalogVersion": "2026.10.01",
    "dateReleased": "2026-10-01T15:00:00.000Z",
    "count": 2,
    "vulnerabilities": [
        {"cveID": "CVE-2021-44228", "vendorProject": "Apache", "product": "Log4j2",
         "vulnerabilityName": "Apache Log4j2 Remote Code Execution Vulnerability",
         "dateAdded": "2021-12-10", "shortDescription": "Log4j2 ...", "requiredAction": "Apply updates.",
         "dueDate": "2021-12-24", "knownRansomwareCampaignUse": "Known", "notes": "", "cwes": ["CWE-917"]},
        {"cveID": "CVE-2020-0001", "vendorProject": "Ejemplo", "product": "Demo",
         "vulnerabilityName": "Demo", "dateAdded": "2022-01-05", "shortDescription": "x",
         "requiredAction": "y", "dueDate": "2022-02-05", "knownRansomwareCampaignUse": "Unknown"},
    ],
}


def _router(nvd_payload, kev_payload=KEV_FIXTURE, kev_status=200):
    def handler(request: httpx.Request):
        if str(request.url).startswith(NVD_URL):
            return json_response(nvd_payload)
        if str(request.url) == KEV_URL:
            if kev_status != 200:
                return text_response("boom", status=kev_status)
            return json_response(kev_payload)
        return json_response({}, status=404)

    return handler


async def test_cve_with_cvss_description_cwe_and_kev_date():
    async with mock_client(_router(NVD_LOG4J)) as client:
        report = await lookup_cve("cve-2021-44228", client=client)
    assert report.cve_id == "CVE-2021-44228"
    assert report.found_in_nvd is True
    assert report.description == "Log4j2 permite ejecución remota (ES)"  # español preferido
    assert report.description_lang == "es"
    assert report.published == "2021-12-10T10:15:09.143"
    assert report.nvd_status == "Analyzed"
    assert report.cvss is not None
    assert (report.cvss.version, report.cvss.score, report.cvss.severity) == ("3.1", 10.0, "CRITICAL")
    assert report.cvss.vector.endswith("S:C/C:H/I:H/A:H")  # la métrica Primary, no la primera
    assert report.cwes == ["CWE-917"]
    assert report.kev_checked is True
    assert report.in_kev is True
    assert report.kev.date_added == date(2021, 12, 10)
    assert report.kev.ransomware_use == "Known"
    types = {r.type for r in report.relations}
    assert types == {"affects"}
    vuln = next(e for e in report.entities if e.type == "vulnerability")
    assert vuln.props["in_kev"] is True
    assert vuln.props["kev_date_added"] == "2021-12-10"


async def test_precomputed_kev_catalog_avoids_second_download():
    catalog = parse_kev(KEV_FIXTURE)
    requested = []

    def handler(request: httpx.Request):
        requested.append(str(request.url))
        return json_response(NVD_LOG4J)

    async with mock_client(handler) as client:
        report = await lookup_cve("CVE-2021-44228", client=client, kev=catalog)
    assert report.in_kev is True
    assert not any(url == KEV_URL for url in requested)


async def test_cve_not_in_kev_is_checked_and_marked_false():
    nvd = {"vulnerabilities": [{"cve": {"id": "CVE-2019-9999", "descriptions": [{"lang": "en", "value": "d"}],
                                       "metrics": {}}}]}
    async with mock_client(_router(nvd)) as client:
        report = await lookup_cve("CVE-2019-9999", client=client)  # no figura en el catálogo de prueba
    assert report.in_kev is False
    assert report.kev is None
    assert report.kev_checked is True
    assert report.cvss is None
    assert report.entities[0].props["cvss_score"] is None


async def test_kev_unavailable_still_returns_nvd_data():
    async with mock_client(_router(NVD_LOG4J, kev_status=503)) as client:
        report = await lookup_cve("CVE-2021-44228", client=client)
    assert report.kev_checked is False
    assert report.in_kev is False
    assert report.found_in_nvd is True
    assert any("catálogo KEV no disponible" in w for w in report.warnings)


async def test_cve_missing_from_nvd_is_reported_not_invented():
    async with mock_client(_router({"totalResults": 0, "vulnerabilities": []})) as client:
        report = await lookup_cve("CVE-1999-0001", client=client)
    assert report.found_in_nvd is False
    assert report.description == ""
    assert report.cvss is None
    assert any("no figura en NVD" in w for w in report.warnings)


async def test_nvd_rate_limit_403_is_retried_and_api_key_is_sent():
    attempts = []
    api_keys = []

    def handler(request: httpx.Request):
        if str(request.url).startswith(NVD_URL):
            attempts.append(1)
            api_keys.append(request.headers.get("apiKey"))
            if len(attempts) == 1:
                return text_response("Forbidden by Administrative Rules", status=403,
                                     headers={"Retry-After": "0"})
            return json_response(NVD_LOG4J)
        return json_response(KEV_FIXTURE)

    async with mock_client(handler) as client:
        report = await lookup_cve("CVE-2021-44228", client=client, api_key="clave-de-prueba")
    assert len(attempts) == 2
    assert api_keys == ["clave-de-prueba", "clave-de-prueba"]
    assert report.found_in_nvd is True


async def test_nvd_persistent_rate_limit_raises():
    def handler(request: httpx.Request):
        return text_response("", status=429, headers={"Retry-After": "0"})

    async with mock_client(handler) as client:
        with pytest.raises(RateLimitedError):
            await lookup_cve("CVE-2021-44228", client=client, kev=parse_kev(KEV_FIXTURE))


@pytest.mark.parametrize("bad", ["2021-44228", "CVE-21-1", "CVE-2021-44228; DROP", "cve", ""])
def test_invalid_cve_ids_are_rejected(bad):
    with pytest.raises(InvalidInputError):
        normalize_cve(bad)


def test_kev_parser_skips_malformed_items_and_keeps_dates():
    data = {"catalogVersion": "x", "dateReleased": "y", "vulnerabilities": [
        {"cveID": "cve-2024-0001", "vendorProject": "V", "product": "P", "dateAdded": "2024-02-03"},
        {"vendorProject": "sin id"},
        "no es un objeto",
    ]}
    catalog = parse_kev(data)
    assert list(catalog.entries) == ["CVE-2024-0001"]
    assert catalog.entries["CVE-2024-0001"].date_added == date(2024, 2, 3)
    assert catalog.skipped == 2
