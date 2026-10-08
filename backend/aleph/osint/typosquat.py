"""Typosquatting: genera variantes de un dominio y comprueba cuáles resuelven por DNS.

Técnicas: omisión, transposición, tecla vecina (QWERTY), homoglifos (confusables ASCII y Unicode),
cambio de TLD, guiones, subdominio (punto insertado o «www» pegado) y bitsquatting (un bit cambiado).

Las variantes Unicode se consultan en su forma punycode (xn--), que es la que usa el DNS.
Solo se hacen consultas DNS de registros A y AAAA: no se visita ningún sitio ni se abre conexión HTTP.
Un registro que resuelve indica que el nombre existe, no que sea malicioso.

Referencia: dnspython, resolución asíncrona: https://dnspython.readthedocs.io/en/stable/resolver-class.html
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

import dns.asyncresolver
import dns.exception
import dns.resolver
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from .errors import DnsLookupError, InvalidInputError

ResolveFn = Callable[[str], Awaitable[list[str]]]
DNS_LIFETIME = 5.0
DEFAULT_CONCURRENCY = 20
MAX_CONCURRENCY = 100
DEFAULT_MAX_VARIANTS = 1500

_HOST_RE = re.compile(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_LDH_LABEL_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")
_MULTIPART_SUFFIXES = frozenset({"com.ar", "net.ar", "org.ar", "gob.ar", "edu.ar", "com.br", "com.mx",
                                 "co.uk", "org.uk", "com.au", "co.jp"})
TLD_CANDIDATES = ("com", "net", "org", "info", "biz", "co", "io", "app", "dev", "online", "site", "xyz",
                  "ar", "com.ar", "net.ar", "org.ar")
_KEY_NEIGHBORS = {
    "q": "wa", "w": "qeasd", "e": "wrsdf", "r": "etdfg", "t": "ryfgh", "y": "tughj", "u": "yihjk",
    "i": "uojkl", "o": "ipkl", "p": "ol", "a": "qwszx", "s": "awedxz", "d": "serfcx", "f": "drtgvc",
    "g": "ftyhbv", "h": "gyujnb", "j": "huiknm", "k": "jiolm", "l": "kop", "z": "asx", "x": "zsdc",
    "c": "xdfv", "v": "cfgb", "b": "vghn", "n": "bhjm", "m": "njk",
}
_ASCII_HOMOGLYPHS = {"o": "0", "0": "o", "l": "1i", "1": "li", "i": "1l"}
_UNICODE_HOMOGLYPHS = {
    "a": "а", "c": "с", "e": "е", "i": "і", "o": "о",
    "p": "р", "x": "х", "y": "у",
}
_MULTI_CHAR_HOMOGLYPHS = (("rn", "m"), ("vv", "w"), ("cl", "d"))


class TypoVariant(BaseModel):
    domain: str  # forma Unicode
    ascii: str  # forma consultada en DNS (punycode si corresponde)
    technique: str
    resolves: bool = False
    addresses: list[str] = Field(default_factory=list)
    error: str = ""


class TyposquatReport(BaseModel):
    domain: str
    generated: int
    checked: int
    resolved: int
    errors: int
    variants: list[TypoVariant] = Field(default_factory=list)
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def normalize_domain(domain: str) -> str:
    value = (domain or "").strip().lower().rstrip(".")
    if not _HOST_RE.fullmatch(value):
        raise InvalidInputError("dominio inválido: se esperan nombres ASCII como example.com")
    return value


def _split_registrable(domain: str) -> tuple[str, str]:
    labels = domain.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTIPART_SUFFIXES:
        return labels[-3], ".".join(labels[-2:])
    return labels[-2], labels[-1]


def _valid_candidate(label: str, *, allow_unicode: bool) -> bool:
    if not label or len(label) > 63 or label.startswith("-") or label.endswith("-"):
        return False
    if allow_unicode:
        return True
    return bool(_LDH_LABEL_RE.fullmatch(label))


def _label_variants(name: str) -> list[tuple[str, str, bool]]:
    """(técnica, etiqueta, admite Unicode) para la etiqueta registrable, sin TLD."""
    out: list[tuple[str, str, bool]] = []
    n = len(name)
    if n > 2:
        for i in range(n):
            out.append(("omisión", name[:i] + name[i + 1:], False))
    for i in range(n - 1):
        if name[i] != name[i + 1]:
            swapped = name[:i] + name[i + 1] + name[i] + name[i + 2:]
            out.append(("transposición", swapped, False))
    for i, ch in enumerate(name):
        for neighbor in _KEY_NEIGHBORS.get(ch, ""):
            out.append(("tecla vecina", name[:i] + neighbor + name[i + 1:], False))
    for i, ch in enumerate(name):
        for lookalike in _ASCII_HOMOGLYPHS.get(ch, ""):
            out.append(("homoglifo", name[:i] + lookalike + name[i + 1:], False))
        if ch in _UNICODE_HOMOGLYPHS:
            out.append(("homoglifo", name[:i] + _UNICODE_HOMOGLYPHS[ch] + name[i + 1:], True))
    for pattern, replacement in _MULTI_CHAR_HOMOGLYPHS:
        start = name.find(pattern)
        while start >= 0:
            out.append(("homoglifo", name[:start] + replacement + name[start + len(pattern):], False))
            start = name.find(pattern, start + 1)
        start = name.find(replacement)
        while start >= 0:
            out.append(("homoglifo", name[:start] + pattern + name[start + 1:], False))
            start = name.find(replacement, start + 1)
    for i in range(1, n):
        out.append(("guion", name[:i] + "-" + name[i:], False))
    if "-" in name:
        out.append(("guion", name.replace("-", ""), False))
    for i in range(1, n):
        out.append(("subdominio", name[:i] + "." + name[i:], False))
    out.append(("subdominio", "www" + name, False))
    for i, ch in enumerate(name):
        for bit in range(8):
            flipped = chr(ord(ch) ^ (1 << bit))
            if re.fullmatch(r"[a-z0-9-]", flipped):
                out.append(("bitsquatting", name[:i] + flipped + name[i + 1:], False))
    return out


def generate_variants(domain: str) -> list[tuple[str, str]]:
    """Variantes (técnica, dominio Unicode) sin duplicados, sin el original y sin validar DNS."""
    base = normalize_domain(domain)
    sld, tld = _split_registrable(base)
    seen: set[str] = {base}
    result: list[tuple[str, str]] = []

    def add(technique: str, label: str, candidate_tld: str, allow_unicode: bool) -> None:
        full = f"{label}.{candidate_tld}"
        if not all(_valid_candidate(part, allow_unicode=allow_unicode) for part in full.split(".")):
            return
        try:
            ascii_form = full.encode("idna").decode("ascii")
        except UnicodeError:
            return
        if len(ascii_form) > 253 or ascii_form in seen:
            return
        seen.add(ascii_form)
        result.append((technique, full))

    for technique, label, unicode_ok in _label_variants(sld):
        add(technique, label, tld, unicode_ok)
    for candidate_tld in TLD_CANDIDATES:
        if candidate_tld != tld:
            add("cambio de TLD", sld, candidate_tld, False)
    return result


async def default_resolver(name: str) -> list[str]:
    """Resuelve A y, si no hay, AAAA con dnspython. Sin registros devuelve []."""
    for rdtype in ("A", "AAAA"):
        try:
            answer = await dns.asyncresolver.resolve(name, rdtype, lifetime=DNS_LIFETIME)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            continue
        except dns.exception.DNSException as exc:
            raise DnsLookupError(f"consulta DNS fallida para {name}: {type(exc).__name__}") from exc
        addresses = [record.to_text() for record in answer]
        if addresses:
            return addresses
    return []


async def analyze_typosquat(
    domain: str,
    *,
    resolver: ResolveFn | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_variants: int = DEFAULT_MAX_VARIANTS,
) -> TyposquatReport:
    """Genera variantes de un dominio y consulta por DNS cuáles resuelven."""
    base = normalize_domain(domain)
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise InvalidInputError(f"concurrency debe estar entre 1 y {MAX_CONCURRENCY}")
    if max_variants < 1:
        raise InvalidInputError("max_variants debe ser mayor que cero")
    generated = generate_variants(base)
    warnings: list[str] = []
    if len(generated) > max_variants:
        warnings.append(f"se generaron {len(generated)} variantes; se consultaron las primeras {max_variants}")
        generated = generated[:max_variants]
    lookup = resolver or default_resolver
    semaphore = asyncio.Semaphore(concurrency)

    async def check(technique: str, unicode_name: str) -> TypoVariant:
        ascii_name = unicode_name.encode("idna").decode("ascii")
        async with semaphore:
            try:
                addresses = await lookup(ascii_name)
            except DnsLookupError as exc:
                return TypoVariant(domain=unicode_name, ascii=ascii_name, technique=technique, error=str(exc))
        return TypoVariant(
            domain=unicode_name, ascii=ascii_name, technique=technique,
            resolves=bool(addresses), addresses=list(addresses),
        )

    variants = await asyncio.gather(*(check(t, d) for t, d in generated))
    resolved = [v for v in variants if v.resolves]
    entities = [EntityRecord(type="domain", label=base, ref="origin", confidence=1.0,
                             props={"rol": "dominio original"})]
    relations: list[RelationRecord] = []
    ip_refs: dict[str, str] = {}
    for index, variant in enumerate(resolved):
        ref = f"variant{index}"
        entities.append(EntityRecord(
            type="domain", label=variant.domain, ref=ref, confidence=1.0,
            props={"idna": variant.ascii, "technique": variant.technique, "addresses": variant.addresses},
        ))
        relations.append(RelationRecord(src_ref=ref, dst_ref="origin", type="lookalike_of", confidence=0.5,
                                        props={"technique": variant.technique}))
        for address in variant.addresses:
            if address not in ip_refs:
                ip_refs[address] = f"ip{len(ip_refs)}"
                entities.append(EntityRecord(type="ip", label=address, ref=ip_refs[address], confidence=1.0))
            relations.append(RelationRecord(src_ref=ref, dst_ref=ip_refs[address], type="resolves_to",
                                            confidence=1.0))
    return TyposquatReport(
        domain=base, generated=len(generated), checked=len(variants), resolved=len(resolved),
        errors=sum(1 for v in variants if v.error), variants=list(variants),
        entities=entities, relations=relations, warnings=warnings,
    )
