"""Extracción, clasificación y defang/refang de IOCs desde texto libre.

Qué reconoce: IPv4, IPv6, dominios (validados contra la lista de TLD de IANA), URLs, correos,
hashes MD5/SHA-1/SHA-256, CVE, direcciones de Bitcoin (Base58Check y Bech32/Bech32m, con
checksum verificado) y Ethereum (formato 0x + 40 hex), y técnicas ATT&CK (solo si el ID existe
en el índice embebido).

Decisiones deliberadas:
- Las IPs privadas, reservadas o de documentación no se descartan: se devuelven con
  `props["scope"]` y `props["routable"] = False`.
- Los nombres de archivo no se toman como dominios. Un TLD que no está en la lista de IANA
  descarta el candidato. Algunos TLD válidos también son extensiones de archivo habituales
  (`.zip`, `.mov`, `.py`, `.sh`...); un nombre así solo cuenta como dominio si el texto viene
  defang (p. ej. `evil[.]zip`) o si tiene prefijo `www.`.
- Los candidatos descartados quedan listados en `IocExtraction.discarded` con su motivo.
- Una vez extraído un IOC, su texto se enmascara para que ningún patrón posterior lo tome
  de nuevo (por ejemplo, el dominio de una URL no aparece también como dominio suelto).
- Ethereum: el checksum EIP-55 requiere keccak-256, que no está en la librería estándar.
  Las direcciones con mayúsculas mixtas se aceptan con `props["eip55"] = "no verificado"`.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from functools import lru_cache
from importlib import resources
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord

from .attack import load_index

# ---------------------------------------------------------------------------
# Refang / defang
# ---------------------------------------------------------------------------

_REFANG_TOKENS = re.compile(
    r"(?P<sep>\[://\]|\(://\))"
    r"|(?P<scheme>\bhxxp(?P<s>s?)(?=://|\[://\]))"
    r"|(?P<dot>(?<=\w)(?:\[\.\]|\(\.\)|\{\.\}|\[dot\]|\(dot\)|\{dot\})(?=\w))"
    r"|(?P<at>(?<=\w)(?:\[@\]|\(@\)|\[at\]|\(at\)|\{at\})(?=\w))"
    r"|(?P<colon>(?<=[0-9A-Fa-f])(?:\[:\]|\(:\))(?=[0-9A-Fa-f]))",
    re.IGNORECASE,
)


def _refang_tracked(text: str) -> tuple[str, set[int]]:
    """Revierte el desarmado y devuelve las posiciones (en el texto nuevo) de los puntos
    que estaban desarmados. Sirve para saber si un dominio candidato fue escrito defang."""
    out: list[str] = []
    restored: set[int] = set()
    last = 0
    size = 0
    for match in _REFANG_TOKENS.finditer(text):
        segment = text[last : match.start()]
        out.append(segment)
        size += len(segment)
        if match.group("sep"):
            replacement = "://"
        elif match.group("scheme"):
            replacement = "https" if match.group("s") else "http"
        elif match.group("dot"):
            replacement = "."
            restored.add(size)
        elif match.group("at"):
            replacement = "@"
        else:
            replacement = ":"
        out.append(replacement)
        size += len(replacement)
        last = match.end()
    out.append(text[last:])
    return "".join(out), restored


def refang(text: str) -> str:
    """Revierte el desarmado habitual: hxxp -> http, [.] -> ., [at] -> @, [:] -> :."""
    return _refang_tracked(text)[0]


def _guess_kind(value: str) -> str:
    low = value.lower()
    if re.match(r"^(https?|ftp)://", low):
        return "url"
    if "@" in value:
        return "email"
    if ":" in value and "/" not in value and "." not in value.split(":")[0]:
        return "ip"
    if re.fullmatch(r"[0-9.]+", value):
        return "ip"
    return "domain"


def defang(value: str, entity_type: str | None = None) -> str:
    """Versión segura para mostrar: hxxp://evil[.]example, user[at]example[.]com, 192[.]0[.]2[.]1.

    Los hashes, CVE y direcciones de cripto no se modifican.
    """
    kind = entity_type or _guess_kind(value)
    if kind == "url":
        match = re.match(r"(?i)^(https?|ftp)://([^/?#]*)(.*)$", value)
        if not match:
            return value
        scheme = match.group(1).lower()
        scheme_out = {"http": "hxxp", "https": "hxxps"}.get(scheme, scheme)
        return f"{scheme_out}://{match.group(2).replace('.', '[.]')}{match.group(3)}"
    if kind == "email":
        local, _, domain = value.rpartition("@")
        return f"{local}[at]{domain.replace('.', '[.]')}"
    if kind == "ip":
        return value.replace(":", "[:]") if ":" in value else value.replace(".", "[.]")
    if kind == "domain":
        return value.replace(".", "[.]")
    return value


# ---------------------------------------------------------------------------
# Datos embebidos y reglas
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _tlds() -> frozenset[str]:
    text = resources.files("aleph.cti").joinpath("data/tlds.txt").read_text(encoding="utf-8")
    return frozenset(
        token
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
        for token in line.split()
    )


# TLD válidos que además son extensiones de archivo muy comunes.
FILE_EXTENSION_TLDS = frozenset({
    "zip", "mov", "py", "sh", "md", "rs", "so", "pl", "ps", "cc", "ai", "cab",
})

_DOC_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),  # RFC 5737
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),  # RFC 3849
)


def ip_scope(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """Clasifica una IP (documentation, loopback, link_local, multicast, unspecified,
    private, public o reserved)."""
    if any(addr in net for net in _DOC_NETWORKS if net.version == addr.version):
        return "documentation"
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link_local"
    if addr.is_multicast:
        return "multicast"
    if addr.is_unspecified:
        return "unspecified"
    if addr.is_private:
        return "private"
    if addr.is_global:
        return "public"
    return "reserved"


# ---------------------------------------------------------------------------
# Expresiones regulares
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"\b(?:https?|ftp)://[^\s<>\"'`]+", re.IGNORECASE)
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])([A-Za-z0-9._%+\-]+)@"
    r"((?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63})(?![A-Za-z0-9\-])"
)
_ETH_RE = re.compile(r"(?<![0-9A-Za-z])0[xX]([0-9A-Fa-f]{40})(?![0-9A-Za-z])")
_BECH32_RE = re.compile(r"(?<![0-9A-Za-z])(bc1[02-9a-z]{6,87})(?![0-9A-Za-z])", re.IGNORECASE)
_BASE58_BTC_RE = re.compile(r"(?<![0-9A-Za-z])([13][1-9A-HJ-NP-Za-km-z]{25,34})(?![0-9A-Za-z])")
_CVE_RE = re.compile(r"(?<![0-9A-Za-z])CVE-((?:19|20)\d{2})-(\d{4,7})(?!\d)", re.IGNORECASE)
_HASH_RE = re.compile(
    r"(?<![0-9A-Za-z])([0-9A-Fa-f]{32}|[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})(?![0-9A-Za-z])"
)
_IPV6_RE = re.compile(r"(?<![0-9A-Za-z:])((?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:.]*)(?![0-9A-Za-z:])")
_IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?!\.?\d)(?![A-Za-z_])")
_DOMAIN_RE = re.compile(
    r"(?<![\w@./:\-])"
    r"((?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+(?:[A-Za-z]{2,63}|xn--[A-Za-z0-9\-]{1,59}))"
    r"(?![\w\-])(?!\.[A-Za-z0-9])"
)
_DOMAIN_SYNTAX_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?$"
)
_ATTACK_RE = re.compile(r"(?<![0-9A-Za-z])T\d{4}(?:\.\d{3})?(?![0-9A-Za-z])")
_ATTACK_URL_RE = re.compile(r"attack\.mitre\.org/techniques/(T\d{4})(?:/(\d{3}))?(?!\d)",
                            re.IGNORECASE)
_VERSION_CONTEXT_RE = re.compile(
    r"(?i)(?:\bver(?:sion|sión)?\.?|\brelease|\bbuild|\brev(?:ision)?|\bpatch)\s*:?\s*$"
)
_TRAILING_URL_CHARS = ".,;:!?)]}>'\""


# ---------------------------------------------------------------------------
# Checksums de Bitcoin
# ---------------------------------------------------------------------------

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


def _base58check_format(address: str) -> str | None:
    """Devuelve 'p2pkh' o 'p2sh' si el checksum Base58Check es válido, o None."""
    num = 0
    try:
        for ch in address:
            num = num * 58 + _B58_ALPHABET.index(ch)
    except ValueError:
        return None
    raw = num.to_bytes((num.bit_length() + 7) // 8, "big")
    raw = b"\x00" * (len(address) - len(address.lstrip("1"))) + raw
    if len(raw) != 25:
        return None
    payload, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        return None
    return {0x00: "p2pkh", 0x05: "p2sh"}.get(payload[0])


def _bech32_polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def _convertbits(data: list[int], frombits: int, tobits: int) -> list[int] | None:
    acc, bits, out = 0, 0, []
    maxv = (1 << tobits) - 1
    for value in data:
        if value < 0 or value >> frombits:
            return None
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & maxv)
    if bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return out


def _segwit_format(address: str) -> str | None:
    """Valida una dirección bc1 (BIP-173 / BIP-350). Devuelve el formato o None."""
    if address != address.lower() and address != address.upper():
        return None  # mayúsculas mezcladas no son válidas
    lower = address.lower()
    pos = lower.rfind("1")
    if pos < 1 or pos + 7 > len(lower) or len(lower) > 90:
        return None
    hrp, data_part = lower[:pos], lower[pos + 1 :]
    if hrp != "bc" or any(c not in _BECH32_CHARSET for c in data_part):
        return None
    data = [_BECH32_CHARSET.index(c) for c in data_part]
    const = _bech32_polymod([ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data)
    version = data[0]
    if version > 16:
        return None
    program = _convertbits(data[1:-6], 5, 8)
    if program is None or not 2 <= len(program) <= 40:
        return None
    if version == 0:
        if const != _BECH32_CONST or len(program) not in (20, 32):
            return None
        return "p2wpkh" if len(program) == 20 else "p2wsh"
    if const != _BECH32M_CONST:
        return None
    return "p2tr" if version == 1 and len(program) == 32 else f"witness-v{version}"


# ---------------------------------------------------------------------------
# Modelos de resultado
# ---------------------------------------------------------------------------


class TechniqueMention(BaseModel):
    """Técnica ATT&CK mencionada en el texto. Solo IDs que existen en el índice embebido."""

    technique_id: str
    name: str
    tactics: list[str] = Field(default_factory=list)  # shortnames, p. ej. "execution"
    url: str = ""
    is_subtechnique: bool = False
    occurrences: int = 1


class Discarded(BaseModel):
    """Candidato que se descartó, con el motivo. Nada se pierde en silencio."""

    kind: str
    value: str
    reason: str


class IocExtraction(BaseModel):
    entities: list[EntityRecord] = Field(default_factory=list)
    techniques: list[TechniqueMention] = Field(default_factory=list)
    discarded: list[Discarded] = Field(default_factory=list)
    defanged_input: bool = False  # True si el texto traía indicadores desarmados


def _mask(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def _entity(kind: str, value: str, props: dict[str, Any], confidence: float) -> EntityRecord:
    props = {**props, "defanged": defang(value, kind), "occurrences": 1}
    return EntityRecord(type=kind, label=value, props=props, confidence=confidence,
                        ref=f"{kind}:{value}")


def _ip_entity(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> EntityRecord:
    scope = ip_scope(addr)
    return _entity("ip", str(addr), {"value": str(addr), "version": addr.version,
                                     "scope": scope, "routable": scope == "public"}, 1.0)


def _domain_entity(host: str, confidence: float, source: str = "") -> EntityRecord:
    props: dict[str, Any] = {"value": host, "tld": host.rsplit(".", 1)[-1]}
    if source:
        props["source"] = source
    return _entity("domain", host, props, confidence)


def _hash_entity(value: str) -> EntityRecord:
    algorithm = {32: "MD5", 40: "SHA-1", 64: "SHA-256"}[len(value)]
    return _entity("hash", value, {"algorithm": algorithm, "value": value,
                                   "hashes": {algorithm: value}}, 0.9)


def _add_occurrence(
    found: dict[str, tuple[int, EntityRecord]], start: int, entity: EntityRecord
) -> None:
    if entity.ref in found:
        first, existing = found[entity.ref]
        existing.props["occurrences"] = existing.props.get("occurrences", 1) + 1
        found[entity.ref] = (min(first, start), existing)
    else:
        found[entity.ref] = (start, entity)


def extract_iocs(text: str) -> IocExtraction:
    """Extrae IOCs y técnicas ATT&CK de un texto libre, con refang previo."""
    original, explicit_dots = _refang_tracked(text)
    defanged_input = original != text
    work = original

    def explicit(start: int, end: int) -> bool:
        """True si algún punto del candidato venía desarmado (p. ej. evil[.]zip)."""
        return any(start <= pos < end for pos in explicit_dots)
    found: dict[str, tuple[int, EntityRecord]] = {}
    discarded: list[Discarded] = []
    tlds = _tlds()

    # 1. URLs: su host (dominio o IP) también se registra como indicador propio.
    for match in _URL_RE.finditer(work):
        url = match.group(0)
        start, end = match.span()
        work = _mask(work, start, end)
        while url and url[-1] in _TRAILING_URL_CHARS:
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            url = url[:-1]
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            _ = parts.port  # levanta ValueError si el puerto es inválido
        except ValueError:
            discarded.append(Discarded(kind="url", value=url, reason="URL mal formada"))
            continue
        if not host:
            discarded.append(Discarded(kind="url", value=url, reason="URL sin host"))
            continue
        scheme = parts.scheme.lower()
        canonical = urlunsplit((scheme, parts.netloc.lower(), parts.path, parts.query,
                                parts.fragment))
        _add_occurrence(found, start, _entity("url", canonical, {
            "value": canonical, "scheme": scheme, "host": host,
            "host_type": "ip" if _is_ip(host) else "domain"}, 0.9))
        if _is_ip(host):
            addr = ipaddress.ip_address(host)
            _add_occurrence(found, start, _ip_entity(addr))
        elif _DOMAIN_SYNTAX_RE.match(host) and host.rsplit(".", 1)[-1] in tlds:
            _add_occurrence(found, start, _domain_entity(host, 0.9, source="url_host"))

    # 2. Correos.
    for match in _EMAIL_RE.finditer(work):
        local, domain = match.group(1), match.group(2).lower()
        start, end = match.span()
        work = _mask(work, start, end)
        if local.startswith(".") or local.endswith(".") or ".." in local:
            discarded.append(Discarded(kind="email", value=match.group(0),
                                       reason="parte local inválida"))
            continue
        if domain.rsplit(".", 1)[-1] not in tlds:
            discarded.append(Discarded(kind="email", value=match.group(0),
                                       reason="dominio de correo con TLD desconocido"))
            continue
        value = f"{local}@{domain}"
        _add_occurrence(found, start, _entity("email", value, {"value": value, "domain": domain},
                                              0.9))

    # 3. Ethereum (0x + 40 hex).
    for match in _ETH_RE.finditer(work):
        start, end = match.span()
        work = _mask(work, start, end)
        raw = match.group(1)
        mixed = raw != raw.lower() and raw != raw.upper()
        value = "0x" + raw.lower()
        _add_occurrence(found, start, _entity("wallet", value, {
            "currency": "ETH", "address": value,
            "eip55": "no verificado (mayúsculas mixtas)" if mixed else "no aplica"}, 0.9))

    # 4. Bitcoin: Bech32/Bech32m y Base58Check, ambos con checksum verificado.
    for match in _BECH32_RE.finditer(work):
        start, end = match.span()
        work = _mask(work, start, end)
        address = match.group(1)
        fmt = _segwit_format(address)
        if fmt is None:
            discarded.append(Discarded(kind="wallet", value=address,
                                       reason="checksum Bech32 inválido"))
            continue
        _add_occurrence(found, start, _entity("wallet", address.lower(), {
            "currency": "BTC", "address": address.lower(), "format": fmt,
            "checksum": "verificado"}, 1.0))
    for match in _BASE58_BTC_RE.finditer(work):
        start, end = match.span()
        work = _mask(work, start, end)
        address = match.group(1)
        fmt = _base58check_format(address)
        if fmt is None:
            discarded.append(Discarded(kind="wallet", value=address,
                                       reason="checksum Base58Check inválido"))
            continue
        _add_occurrence(found, start, _entity("wallet", address, {
            "currency": "BTC", "address": address, "format": fmt,
            "checksum": "verificado"}, 1.0))

    # 5. CVE.
    for match in _CVE_RE.finditer(work):
        start, end = match.span()
        work = _mask(work, start, end)
        cve = f"CVE-{match.group(1)}-{match.group(2)}"
        _add_occurrence(found, start, _entity("vulnerability", cve, {"cve_id": cve}, 1.0))

    # 6. Hashes: MD5, SHA-1, SHA-256. Sin letras a-f no parece un hash (p. ej., un número).
    for match in _HASH_RE.finditer(work):
        start, end = match.span()
        work = _mask(work, start, end)
        raw = match.group(1)
        if not re.search(r"[A-Fa-f]", raw):
            discarded.append(Discarded(kind="hash", value=raw,
                                       reason="solo dígitos: no parece un hash"))
            continue
        _add_occurrence(found, start, _hash_entity(raw.lower()))

    # 7. IPv6 (valida con ipaddress; lo que no es IPv6 se ignora sin ruido).
    for match in _IPV6_RE.finditer(work):
        start, end = match.span()
        work = _mask(work, start, end)
        candidate = match.group(1).rstrip(".")  # el punto final de la oración no es parte de la IP
        try:
            addr: ipaddress.IPv6Address | ipaddress.IPv4Address = ipaddress.IPv6Address(candidate)
        except ValueError:
            continue
        _add_occurrence(found, start, _ip_entity(addr))

    # 8. IPv4. Versiones de software (v1.2.3.4, "versión 1.2.3.4", Apache/2.4.1.2) se descartan.
    for match in _IPV4_RE.finditer(work):
        start, end = match.span(1)
        work = _mask(work, start, end)
        raw = match.group(1)
        before = original[start - 1] if start > 0 else ""
        if before.isalpha() or before in ("_", "/", "-"):
            discarded.append(Discarded(kind="ip", value=raw,
                                       reason="pegado a texto o a un separador: probable versión"))
            continue
        window = original[max(0, start - 20) : start]
        if _VERSION_CONTEXT_RE.search(window):
            discarded.append(Discarded(kind="ip", value=raw,
                                       reason="contexto de versión de software"))
            continue
        try:
            addr = ipaddress.IPv4Address(raw)
        except ValueError:
            discarded.append(Discarded(kind="ip", value=raw, reason="octeto fuera de rango"))
            continue
        _add_occurrence(found, start, _ip_entity(addr))

    # 9. Dominios: TLD obligatorio en la lista de IANA; nombres de archivo fuera.
    for match in _DOMAIN_RE.finditer(work):
        start, end = match.span(1)
        work = _mask(work, start, end)
        host = match.group(1).lower()
        tld = host.rsplit(".", 1)[-1]
        if tld not in tlds:
            discarded.append(Discarded(kind="domain", value=host,
                                       reason="TLD no reconocido (posible nombre de archivo)"))
            continue
        if (tld in FILE_EXTENSION_TLDS and not explicit(start, end)
                and not host.startswith("www.")):
            discarded.append(Discarded(
                kind="domain", value=host,
                reason=f"extensión de archivo ambigua (.{tld}); si es un dominio, escribilo defang",
            ))
            continue
        if len(host) > 253:
            discarded.append(Discarded(kind="domain", value=host, reason="nombre demasiado largo"))
            continue
        _add_occurrence(found, start, _domain_entity(host, 0.8))

    # Técnicas ATT&CK: se buscan en el texto completo (también dentro de URLs).
    index = load_index()
    techniques: dict[str, TechniqueMention] = {}

    def technique_hit(tid: str) -> None:
        tech = index.get(tid)
        if tech is None:
            discarded.append(Discarded(
                kind="attack", value=tid,
                reason=f"ID ATT&CK inexistente en la versión {index.version} embebida",
            ))
        elif tid in techniques:
            techniques[tid].occurrences += 1
        else:
            techniques[tid] = TechniqueMention(
                technique_id=tid, name=tech.name, tactics=list(tech.tactics), url=tech.url,
                is_subtechnique=tech.is_subtechnique,
            )

    # Las URL de ATT&CK usan barra para la subtécnica: .../techniques/T1566/001
    url_spans: list[tuple[int, int]] = []
    for match in _ATTACK_URL_RE.finditer(original):
        url_spans.append(match.span())
        base, sub = match.group(1).upper(), match.group(2)
        technique_hit(f"{base}.{sub}" if sub else base)
    for match in _ATTACK_RE.finditer(original):
        if not any(start <= match.start() < end for start, end in url_spans):
            technique_hit(match.group(0))

    ordered = sorted(found.values(), key=lambda pair: pair[0])
    return IocExtraction(
        entities=[entity for _, entity in ordered],
        techniques=list(techniques.values()),
        discarded=discarded,
        defanged_input=defanged_input,
    )


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
