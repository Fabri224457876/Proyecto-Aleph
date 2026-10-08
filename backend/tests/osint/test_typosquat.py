import asyncio
import re

import pytest

from aleph.osint.errors import DnsLookupError, InvalidInputError
from aleph.osint.typosquat import analyze_typosquat, generate_variants, normalize_domain

HOST_RE = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def _domains(base="example.com"):
    return {domain for _, domain in generate_variants(base)}


def test_variants_cover_every_technique_family():
    pairs = generate_variants("example.com")
    names = {domain for _, domain in pairs}
    techniques = {technique for technique, _ in pairs}
    assert {"omisión", "transposición", "tecla vecina", "homoglifo", "cambio de TLD", "guion",
            "subdominio", "bitsquatting"} <= techniques
    assert "exmaple.com" in names          # transposición
    assert "exampl.com" in names           # omisión
    assert "ezample.com" in names          # x → z (tecla vecina)
    assert "ex-ample.com" in names         # guion
    assert "example.net" in names          # cambio de TLD
    assert "wwwexample.com" in names       # «www» pegado
    assert "ex.ample.com" in names         # punto insertado (subdominio)
    assert "dxample.com" in names          # bitsquatting: 'e' (0x65) con el bit 0 cambiado


def test_unicode_homoglyph_is_generated_and_queried_as_punycode():
    variants = [(t, d) for t, d in generate_variants("example.com") if t == "homoglifo"]
    unicode_names = [d for _, d in variants if not d.isascii()]
    assert unicode_names, "debe haber al menos una variante con confusables Unicode"
    for name in unicode_names:
        ascii_form = name.encode("idna").decode("ascii")
        assert ascii_form.startswith("xn--")
        assert HOST_RE.fullmatch(ascii_form)


def test_original_domain_is_excluded_and_no_duplicates():
    domains = [d for _, d in generate_variants("example.com")]
    assert "example.com" not in domains
    assert len(domains) == len(set(domains))


def test_all_generated_names_are_valid_hostnames():
    for domain in _domains("paypal.com.ar"):
        ascii_form = domain.encode("idna").decode("ascii")
        assert HOST_RE.fullmatch(ascii_form), ascii_form
    assert "paypal.net.ar" in _domains("paypal.com.ar")  # sufijo compuesto respetado


def test_normalize_rejects_bad_names():
    assert normalize_domain(" Example.COM. ") == "example.com"
    for bad in ["", "localhost", "no es un dominio", "a..b.com", "-x.com", "x" * 70 + ".com"]:
        with pytest.raises(InvalidInputError):
            normalize_domain(bad)


async def test_dns_resolution_marks_resolving_variants_and_builds_graph():
    resolved_names = {"exmaple.com": ["192.0.2.10"], "example.net": ["192.0.2.10", "2001:db8::10"]}

    async def fake_resolver(name):
        return resolved_names.get(name, [])

    report = await analyze_typosquat("example.com", resolver=fake_resolver)
    assert report.checked == report.generated
    assert report.resolved == 2
    resolved = {v.ascii: v for v in report.variants if v.resolves}
    assert set(resolved) == {"exmaple.com", "example.net"}
    assert resolved["example.net"].addresses == ["192.0.2.10", "2001:db8::10"]
    origin = next(e for e in report.entities if e.ref == "origin")
    assert origin.label == "example.com"
    lookalikes = [r for r in report.relations if r.type == "lookalike_of"]
    assert len(lookalikes) == 2 and all(r.dst_ref == "origin" for r in lookalikes)
    ip_entities = [e for e in report.entities if e.type == "ip"]
    assert sorted(e.label for e in ip_entities) == ["192.0.2.10", "2001:db8::10"]  # sin duplicar


async def test_dns_errors_are_counted_apart_from_negative_answers():
    async def flaky(name):
        if name == "exampl.com":
            raise DnsLookupError("timeout")
        return []

    report = await analyze_typosquat("example.com", resolver=flaky)
    assert report.errors == 1
    assert report.resolved == 0
    failed = [v for v in report.variants if v.error]
    assert failed and failed[0].ascii == "exampl.com"


async def test_concurrency_limit_is_respected():
    in_flight = 0
    peak = 0

    async def tracking(name):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return []

    report = await analyze_typosquat("example.com", resolver=tracking, concurrency=3)
    assert report.checked > 3
    assert peak <= 3


async def test_max_variants_truncates_with_warning():
    async def none(name):
        return []

    report = await analyze_typosquat("example.com", resolver=none, max_variants=5)
    assert report.checked == 5
    assert report.generated == 5
    assert report.warnings


async def test_invalid_parameters_rejected():
    async def none(name):
        return []

    with pytest.raises(InvalidInputError):
        await analyze_typosquat("example.com", resolver=none, concurrency=0)
    with pytest.raises(InvalidInputError):
        await analyze_typosquat("example.com", resolver=none, concurrency=1000)
    with pytest.raises(InvalidInputError):
        await analyze_typosquat("no es dominio", resolver=none)
