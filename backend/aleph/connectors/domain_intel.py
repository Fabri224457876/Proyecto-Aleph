"""Inteligencia de dominio: RDAP, DNS y subdominios desde certificate transparency (crt.sh).

Fuentes públicas, sin autenticación:
- RDAP (formato de respuesta JSON de RFC 9083, https://www.rfc-editor.org/rfc/rfc9083; de memoria).
  Se consulta a través del redirector https://rdap.org/domain/<dominio>, que sigue la redirección al
  registro autoritativo. Muchos registros ocultan el titular ("REDACTED FOR PRIVACY"): esos datos
  se descartan.
- DNS: registros A, AAAA, MX, NS y TXT con dnspython (https://www.dnspython.org/).
- crt.sh: https://crt.sh/?q=%25.<dominio>&output=json (servicio no oficial; el campo name_value trae
  los nombres del certificado separados por saltos de línea, con comodines "*.").

Sin verificar contra respuestas reales en este entorno: el JSON de crt.sh, el redirector rdap.org y
la forma exacta de las vCard que devuelve cada registro. Los tests no hacen consultas DNS reales.
"""

import ipaddress
import re
from collections import Counter
from datetime import datetime
from typing import Any, ClassVar

import dns.asyncresolver
import dns.exception
import dns.resolver

from aleph.connectors._util import (
    HttpSession,
    parse_datetime,
    positive_int,
    require_text,
    utcnow,
)
from aleph.connectors.base import Connector, ConnectorError, register
from aleph.core.schemas import CollectionResult, EntityRecord, RelationRecord

RDAP_BASE = "https://rdap.org/domain/"
CRTSH_URL = "https://crt.sh/"
DNS_LIFETIME = 8.0
DNS_RECORD_TYPES = ("A", "AAAA", "MX", "NS", "TXT")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _clean_domain(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^[a-z]+://", "", text)
    text = text.split("/", 1)[0].split(":", 1)[0].rstrip(".")
    text = text.removeprefix("*.")
    try:
        text = text.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ConnectorError(f"dominio inválido: {value!r}") from exc
    if not _DOMAIN_RE.match(text):
        raise ConnectorError(f"dominio inválido: {value!r}")
    return text


def _is_redacted(value: str) -> bool:
    return not value or "REDACTED" in value.upper()


def _rdap_entities(rdap: dict[str, Any]) -> list[dict[str, Any]]:
    """Todas las entidades RDAP, incluidas las anidadas."""
    found: list[dict[str, Any]] = []

    def visit(items: Any) -> None:
        for item in items or []:
            if isinstance(item, dict):
                found.append(item)
                visit(item.get("entities"))

    visit(rdap.get("entities"))
    return found


def _vcard(entity: dict[str, Any], field: str) -> str:
    """Valor de una propiedad de la vCard de una entidad RDAP (fn, org, email...)."""
    card = entity.get("vcardArray")
    if not isinstance(card, list) or len(card) < 2 or not isinstance(card[1], list):
        return ""
    for prop in card[1]:
        if isinstance(prop, list) and len(prop) >= 4 and prop[0] == field:
            value = prop[3]
            if isinstance(value, list):
                value = " ".join(str(v) for v in value if v)
            return str(value or "").strip()
    return ""


def _rdap_dates(rdap: dict[str, Any]) -> dict[str, str]:
    dates: dict[str, str] = {}
    names = {
        "registration": "registered_at",
        "expiration": "expires_at",
        "last changed": "updated_at",
    }
    for event in rdap.get("events") or []:
        key = names.get(str(event.get("eventAction") or ""))
        when = parse_datetime(event.get("eventDate"))
        if key and when:
            dates[key] = when.isoformat()
    return dates


@register
class DomainIntelConnector(Connector):
    name = "domain_intel"
    title = "Inteligencia de dominio (RDAP, DNS y certificate transparency)"
    mode = "live"
    params: ClassVar[dict[str, str]] = {
        "domain": "Dominio a investigar, p. ej. ejemplo.com",
        "limit": "Máximo de subdominios a listar desde crt.sh (por defecto 200)",
    }

    async def collect(self, *, domain: str, limit: int = 200) -> CollectionResult:
        name = _clean_domain(require_text(domain, "domain"))
        limit = positive_int(limit, "limit", 200)
        root_ref = f"domain:{name}"
        warnings: list[str] = []
        raw: dict[str, Any] = {"rdap": None, "dns": {}, "crtsh": None}
        ents: dict[str, EntityRecord] = {}
        relations: dict[tuple[str, str, str], RelationRecord] = {}
        root = EntityRecord(type="domain", label=name, props={}, confidence=1.0, ref=root_ref)
        ents[root_ref] = root

        def relate(dst_ref: str, rel_type: str, src_ref: str = root_ref, **props: Any) -> None:
            key = (src_ref, dst_ref, rel_type)
            if key not in relations:
                relations[key] = RelationRecord(
                    src_ref=src_ref,
                    dst_ref=dst_ref,
                    type=rel_type,
                    props=props,
                    confidence=1.0,
                )

        async with HttpSession(
            self.client, timeout=15.0, max_retries=1, max_retry_after=10.0
        ) as http:
            rdap = await self._rdap(http, name, warnings)
            raw["rdap"] = rdap
            if rdap:
                root.props.update(_rdap_dates(rdap))
                root.props["status"] = rdap.get("status") or []
                root.props["rdap_handle"] = rdap.get("handle")
                self._apply_rdap(rdap, ents, relate)

            dns_values: dict[str, list[str]] = {}
            for rdtype in DNS_RECORD_TYPES:
                try:
                    dns_values[rdtype] = await self._resolve(name, rdtype)
                except ConnectorError as exc:
                    dns_values[rdtype] = []
                    warnings.append(str(exc))
            raw["dns"] = dns_values
            self._apply_dns(dns_values, root, ents, relate)

            subdomains, crt_raw = await self._crtsh(http, name, limit, warnings)
            raw["crtsh"] = crt_raw
            for host, count, first_seen in subdomains:
                ref = f"domain:{host}"
                ents.setdefault(
                    ref,
                    EntityRecord(
                        type="domain",
                        label=host,
                        props={
                            "source": "crt.sh",
                            "certificates": count,
                            "first_certificate_at": first_seen.isoformat() if first_seen else None,
                        },
                        confidence=0.9,
                        ref=ref,
                    ),
                )
                relate(root_ref, "subdomain_of", src_ref=ref)

        return CollectionResult(
            connector=self.name,
            reference=name,
            retrieved_at=utcnow(),
            entities=list(ents.values()),
            relations=list(relations.values()),
            warnings=warnings,
            raw=raw,
        )

    async def _rdap(
        self, http: HttpSession, name: str, warnings: list[str]
    ) -> dict[str, Any] | None:
        try:
            data = await http.get_json(
                f"{RDAP_BASE}{name}", headers={"Accept": "application/rdap+json"}
            )
        except ConnectorError as exc:
            warnings.append(f"RDAP no disponible: {exc}")
            return None
        if data is None:
            warnings.append("RDAP: no hay registro para el dominio (HTTP 404)")
            return None
        return data if isinstance(data, dict) else None

    def _apply_rdap(self, rdap: dict[str, Any], ents: dict[str, EntityRecord], relate: Any) -> None:
        for entity in _rdap_entities(rdap):
            roles = {str(r) for r in entity.get("roles") or []}
            org = _vcard(entity, "org")
            person = _vcard(entity, "fn")
            email = _vcard(entity, "email").lower()

            if "registrant" in roles:
                if org and not _is_redacted(org):
                    ref = f"organization:{org.lower()}"
                    ents.setdefault(
                        ref,
                        EntityRecord(
                            type="organization",
                            label=org,
                            props={"source": "rdap"},
                            confidence=0.7,
                            ref=ref,
                        ),
                    )
                    relate(ref, "registered_by", role="registrant")
                elif person and not _is_redacted(person):
                    ref = f"person:{person.lower()}"
                    ents.setdefault(
                        ref,
                        EntityRecord(
                            type="person",
                            label=person,
                            props={"source": "rdap"},
                            confidence=0.6,
                            ref=ref,
                        ),
                    )
                    relate(ref, "registered_by", role="registrant")
            if "registrar" in roles and org and not _is_redacted(org):
                ref = f"organization:{org.lower()}"
                ents.setdefault(
                    ref,
                    EntityRecord(
                        type="organization",
                        label=org,
                        props={"source": "rdap"},
                        confidence=0.9,
                        ref=ref,
                    ),
                )
                relate(ref, "registrar")
            if "@" in email and not _is_redacted(email):
                ref = f"email:{email}"
                ents.setdefault(
                    ref,
                    EntityRecord(
                        type="email",
                        label=email,
                        props={"source": "rdap", "roles": sorted(roles)},
                        confidence=0.6,
                        ref=ref,
                    ),
                )
                relate(ref, "contact_email", role=",".join(sorted(roles)))

        for ns in rdap.get("nameservers") or []:
            host = str(ns.get("ldhName") or "").lower().rstrip(".")
            if _DOMAIN_RE.match(host):
                ref = f"domain:{host}"
                ents.setdefault(
                    ref,
                    EntityRecord(
                        type="domain",
                        label=host,
                        props={"source": "rdap"},
                        confidence=1.0,
                        ref=ref,
                    ),
                )
                relate(ref, "nameserver")

    def _apply_dns(
        self,
        values: dict[str, list[str]],
        root: EntityRecord,
        ents: dict[str, EntityRecord],
        relate: Any,
    ) -> None:
        for addr in values.get("A", []) + values.get("AAAA", []):
            try:
                ip = str(ipaddress.ip_address(addr))
            except ValueError:
                continue
            ref = f"ip:{ip}"
            ents.setdefault(
                ref,
                EntityRecord(
                    type="ip",
                    label=ip,
                    props={"source": "dns"},
                    confidence=1.0,
                    ref=ref,
                ),
            )
            relate(ref, "resolves_to", record="AAAA" if ":" in ip else "A")

        for host in values.get("MX", []):
            self._host_entity(host, "mail_exchanger", ents, relate)
        for host in values.get("NS", []):
            self._host_entity(host, "nameserver", ents, relate)
        root.props["txt"] = values.get("TXT", [])[:20]

    @staticmethod
    def _host_entity(host: str, rel_type: str, ents: dict[str, EntityRecord], relate: Any) -> None:
        clean = host.lower().rstrip(".")
        if not _DOMAIN_RE.match(clean):
            return
        ref = f"domain:{clean}"
        ents.setdefault(
            ref,
            EntityRecord(
                type="domain",
                label=clean,
                props={"source": "dns"},
                confidence=1.0,
                ref=ref,
            ),
        )
        relate(ref, rel_type)

    async def _resolve(self, name: str, rdtype: str) -> list[str]:
        """Valores de un registro DNS. Es el único punto de red DNS; los tests lo reemplazan."""
        try:
            answer = await dns.asyncresolver.resolve(name, rdtype, lifetime=DNS_LIFETIME)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.exception.DNSException as exc:
            raise ConnectorError(f"consulta DNS {rdtype} fallida ({type(exc).__name__})") from exc
        values: list[str] = []
        for rdata in answer:
            if rdtype in ("A", "AAAA"):
                values.append(str(rdata.address))
            elif rdtype == "MX":
                values.append(rdata.exchange.to_text().rstrip("."))
            elif rdtype == "NS":
                values.append(rdata.target.to_text().rstrip("."))
            elif rdtype == "TXT":
                values.append(b"".join(rdata.strings).decode("utf-8", errors="replace"))
        return [v for v in values if v]

    async def _crtsh(
        self, http: HttpSession, name: str, limit: int, warnings: list[str]
    ) -> tuple[list[tuple[str, int, datetime | None]], Any]:
        try:
            data = await http.get_json(CRTSH_URL, params={"q": f"%.{name}", "output": "json"})
        except ConnectorError as exc:
            warnings.append(f"crt.sh no disponible: {exc}")
            return [], None
        rows = data if isinstance(data, list) else []
        counts: Counter[str] = Counter()
        first_seen: dict[str, datetime] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            when = parse_datetime(row.get("entry_timestamp") or row.get("not_before"))
            for line in str(row.get("name_value") or "").splitlines():
                host = line.strip().lower()
                host = host.removeprefix("*.")
                if host == name or not host.endswith(f".{name}") or not _DOMAIN_RE.match(host):
                    continue
                counts[host] += 1
                if when and (host not in first_seen or when < first_seen[host]):
                    first_seen[host] = when
        subdomains = [(host, counts[host], first_seen.get(host)) for host in sorted(counts)[:limit]]
        return subdomains, data
