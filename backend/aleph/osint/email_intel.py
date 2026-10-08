"""Análisis de una dirección de email sin contactar a su titular: sintaxis, registros DNS del dominio
(MX, SPF y DMARC), dominio descartable y URL de avatar de Gravatar.

Referencias:
- Gravatar, hash del email: https://docs.gravatar.com/api/avatars/hash/ (SHA-256 del email sin espacios
  y en minúsculas). Formato de imagen: https://docs.gravatar.com/api/avatars/images/
  La URL se construye; no se consulta.
- dnspython, resolución asíncrona: https://dnspython.readthedocs.io/en/stable/

Qué NO hace: no verifica si la cuenta existe. No abre conexiones SMTP con el buzón ni usa formularios de
registro o de recuperación de contraseña de terceros. Las consultas DNS son a los registros públicos del dominio.
La lista de dominios descartables es parcial y embebida: un dominio que no figura no es prueba de lo contrario.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable

import dns.asyncresolver
import dns.exception
import dns.resolver
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from .errors import DnsLookupError, InvalidInputError

DNS_LIFETIME = 5.0
MAX_EMAIL_LEN = 320
GRAVATAR_BASE = "https://gravatar.com/avatar/"
DISPOSABLE_DOMAINS = frozenset({
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "10minutemail.com", "tempmail.com",
    "temp-mail.org", "yopmail.com", "trashmail.com", "throwawaymail.com", "getnada.com",
    "sharklasers.com", "dispostable.com", "maildrop.cc", "mintemail.com", "fakeinbox.com",
    "mohmal.com", "emailondeck.com", "spambog.com", "tempinbox.com", "mailnesia.com",
})
_LOCAL_RE = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*")
_LABEL_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_DMARC_POLICY_RE = re.compile(r"(?:^|;)\s*p\s*=\s*([A-Za-z]+)", re.IGNORECASE)

DnsFn = Callable[[str, str], Awaitable[list[str]]]  # (nombre, tipo) → registros en texto


class MxRecord(BaseModel):
    preference: int
    host: str


class EmailIntelResult(BaseModel):
    address: str
    normalized: str
    local_part: str
    domain: str
    domain_ascii: str
    syntax_valid: bool
    syntax_problems: list[str] = Field(default_factory=list)
    dns_status: str  # consultado | no consultado (sintaxis inválida) | error DNS
    mx_status: str  # con registros MX | sin registros MX | no consultado | error DNS
    mx: list[MxRecord] = Field(default_factory=list)
    spf: str | None = None
    dmarc: str | None = None
    dmarc_policy: str = ""
    disposable: bool = False
    gravatar_sha256: str
    gravatar_url: str
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


async def default_dns(name: str, rdtype: str) -> list[str]:
    """Registros en texto con dnspython. Sin registros devuelve []; fallos de red lanzan DnsLookupError."""
    try:
        answer = await dns.asyncresolver.resolve(name, rdtype, lifetime=DNS_LIFETIME)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except dns.exception.DNSException as exc:
        raise DnsLookupError(f"consulta {rdtype} fallida para {name}: {type(exc).__name__}") from exc
    records: list[str] = []
    for record in answer:
        if rdtype == "MX":
            records.append(f"{record.preference} {record.exchange.to_text()}")
        elif rdtype == "TXT":
            records.append(b"".join(record.strings).decode("utf-8", errors="replace"))
        else:
            records.append(record.to_text())
    return records


def _validate_syntax(address: str) -> tuple[str, str, list[str]]:
    problems: list[str] = []
    if address.count("@") != 1:
        return "", "", ["debe contener exactamente un símbolo @"]
    local, domain = address.split("@")
    domain = domain.lower()  # los nombres de dominio no distinguen mayúsculas; el DNS se consulta en minúsculas
    if not local or len(local) > 64 or not _LOCAL_RE.fullmatch(local):
        problems.append("parte local inválida (longitud o caracteres)")
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return local, domain, problems + ["dominio con caracteres no válidos"]
    labels = ascii_domain.split(".")
    if len(labels) < 2 or len(ascii_domain) > 253:
        problems.append("el dominio debe tener al menos dos etiquetas y no superar 253 caracteres")
    elif not all(_LABEL_RE.fullmatch(label) for label in labels):
        problems.append("etiqueta de dominio inválida")
    elif not re.fullmatch(r"[A-Za-z]{2,63}", labels[-1]):
        problems.append("el dominio de nivel superior debe ser alfabético de al menos dos letras")
    return local, ascii_domain, problems


def _is_disposable(domain: str) -> bool:
    return any(domain == item or domain.endswith("." + item) for item in DISPOSABLE_DOMAINS)


async def analyze_email(address: str, *, resolver: DnsFn | None = None) -> EmailIntelResult:
    """Análisis de sintaxis, DNS y reputación básica de un email, sin contactar al titular."""
    text = (address or "").strip()
    if not text or len(text) > MAX_EMAIL_LEN:
        raise InvalidInputError("email vacío o demasiado largo")
    local, domain_ascii, problems = _validate_syntax(text)
    domain = text.rsplit("@", 1)[1].lower() if "@" in text else ""
    digest = hashlib.sha256(text.lower().encode("utf-8")).hexdigest()
    gravatar = GRAVATAR_BASE + digest
    warnings: list[str] = []
    mx_records: list[MxRecord] = []
    spf: str | None = None
    dmarc: str | None = None
    dmarc_policy = ""
    dns_status = "no consultado (sintaxis inválida)"
    mx_status = "no consultado"

    if not problems:
        lookup = resolver or default_dns
        dns_status = "consultado"
        try:
            mx_answers = await lookup(domain_ascii, "MX")
        except DnsLookupError as exc:
            dns_status = "error DNS"
            mx_status = "error DNS"
            warnings.append(str(exc))
        else:
            mx_records = sorted(
                (_parse_mx(answer) for answer in mx_answers), key=lambda record: record.preference,
            )
            mx_status = "con registros MX" if mx_records else "sin registros MX"
            try:
                txt_answers, dmarc_answers = await asyncio.gather(
                    lookup(domain_ascii, "TXT"), lookup(f"_dmarc.{domain_ascii}", "TXT"),
                )
            except DnsLookupError as exc:
                dns_status = "error DNS"
                warnings.append(str(exc))
            else:
                spf = next((t for t in txt_answers if t.lower().startswith("v=spf1")), None)
                dmarc = next((t for t in dmarc_answers if t.lower().startswith("v=dmarc1")), None)
                if dmarc:
                    match = _DMARC_POLICY_RE.search(dmarc)
                    dmarc_policy = match.group(1).lower() if match else ""
    else:
        warnings.append("la sintaxis no es válida: no se consultó el DNS del dominio")

    disposable = bool(domain) and _is_disposable(domain)
    entities: list[EntityRecord] = [EntityRecord(
        type="email", label=text.lower(), ref="email", confidence=1.0,
        props={"syntax_valid": not problems, "gravatar_url": gravatar, "disposable": disposable},
    )]
    relations: list[RelationRecord] = []
    if domain_ascii and not problems:
        entities.append(EntityRecord(
            type="domain", label=domain_ascii, ref="domain", confidence=1.0,
            props={"mx": [record.host for record in mx_records], "spf": spf or "",
                   "dmarc_policy": dmarc_policy, "disposable": disposable},
        ))
        relations.append(RelationRecord(src_ref="email", dst_ref="domain", type="belongs_to_domain",
                                        confidence=1.0))
        for index, record in enumerate(mx_records):
            ref = f"mx{index}"
            entities.append(EntityRecord(type="domain", label=record.host.rstrip("."), ref=ref,
                                         confidence=1.0, props={"rol": "servidor de correo (MX)"}))
            relations.append(RelationRecord(src_ref=ref, dst_ref="domain", type="mail_server_of",
                                            confidence=1.0))

    return EmailIntelResult(
        address=text, normalized=text.lower(), local_part=local, domain=domain, domain_ascii=domain_ascii,
        syntax_valid=not problems, syntax_problems=problems, dns_status=dns_status, mx_status=mx_status,
        mx=mx_records, spf=spf, dmarc=dmarc, dmarc_policy=dmarc_policy, disposable=disposable,
        gravatar_sha256=digest, gravatar_url=gravatar, entities=entities, relations=relations,
        warnings=warnings,
    )


def _parse_mx(answer: str) -> MxRecord:
    preference, _, host = answer.partition(" ")
    return MxRecord(preference=int(preference), host=host.strip().rstrip("."))
