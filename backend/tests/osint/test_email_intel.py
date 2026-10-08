import hashlib

import pytest

from aleph.osint.email_intel import analyze_email
from aleph.osint.errors import DnsLookupError, InvalidInputError

RECORDS = {
    ("example.com", "MX"): ["20 mx2.example.com.", "10 mx1.example.com."],
    ("example.com", "TXT"): ["google-site-verification=abc", "v=spf1 include:_spf.example.com -all"],
    ("_dmarc.example.com", "TXT"): ["v=DMARC1; p=reject; rua=mailto:dmarc@example.com"],
}


class FakeDns:
    def __init__(self, records=RECORDS, fail=()):
        self.records = records
        self.fail = set(fail)
        self.calls = []

    async def __call__(self, name, rdtype):
        self.calls.append((name, rdtype))
        if (name, rdtype) in self.fail:
            raise DnsLookupError(f"timeout {name} {rdtype}")
        return list(self.records.get((name, rdtype), []))


async def test_full_analysis_of_a_public_style_address():
    dns = FakeDns()
    result = await analyze_email("Persona.Ejemplo+tag@Example.com", resolver=dns)
    assert result.syntax_valid is True
    assert result.normalized == "persona.ejemplo+tag@example.com"
    assert result.local_part == "Persona.Ejemplo+tag"
    assert result.domain == "example.com"
    assert result.dns_status == "consultado"
    assert result.mx_status == "con registros MX"
    assert [m.host for m in result.mx] == ["mx1.example.com", "mx2.example.com"]  # por preferencia
    assert result.spf.startswith("v=spf1")
    assert result.dmarc_policy == "reject"
    assert result.disposable is False


async def test_gravatar_is_built_from_trimmed_lowercase_sha256_without_request():
    dns = FakeDns()
    result = await analyze_email("  Persona.Ejemplo@Example.com  ", resolver=dns)
    expected = hashlib.sha256(b"persona.ejemplo@example.com").hexdigest()
    assert result.gravatar_sha256 == expected
    assert result.gravatar_url == f"https://gravatar.com/avatar/{expected}"
    # Solo se consultó DNS (MX, TXT del dominio y TXT de _dmarc), nunca la URL de Gravatar
    assert {name for name, _ in dns.calls} == {"example.com", "_dmarc.example.com"}


@pytest.mark.parametrize("address", ["a..b@example.com", "sin-arroba.example.com", "x@localhost",
                                     "x@dominio-con-guion-.com", "x@ejemplo.123", "a@b@example.com"])
async def test_invalid_syntax_is_reported_and_dns_is_not_queried(address):
    dns = FakeDns()
    result = await analyze_email(address, resolver=dns)
    assert result.syntax_valid is False
    assert result.syntax_problems
    assert result.dns_status.startswith("no consultado")
    assert dns.calls == []


async def test_domain_without_mx_and_dns_error_are_distinguished():
    dns = FakeDns(records={}, fail={("example.net", "TXT")})
    result = await analyze_email("x@example.net", resolver=dns)
    assert result.mx_status == "sin registros MX"
    assert result.dns_status == "error DNS"
    assert any("timeout" in w for w in result.warnings)


async def test_disposable_domain_flag_including_subdomains():
    dns = FakeDns(records={})
    result = await analyze_email("temp@mailinator.com", resolver=dns)
    assert result.disposable is True
    result = await analyze_email("temp@sub.mailinator.com", resolver=dns)
    assert result.disposable is True
    result = await analyze_email("temp@example.com", resolver=dns)
    assert result.disposable is False


async def test_entities_and_relations_for_the_graph():
    result = await analyze_email("persona@example.com", resolver=FakeDns())
    types = [e.type for e in result.entities]
    assert types.count("email") == 1 and types.count("domain") == 3  # dominio + dos servidores MX
    assert {r.type for r in result.relations} == {"belongs_to_domain", "mail_server_of"}
    email = next(e for e in result.entities if e.type == "email")
    assert email.label == "persona@example.com"


async def test_empty_input_rejected():
    with pytest.raises(InvalidInputError):
        await analyze_email("   ", resolver=FakeDns())
