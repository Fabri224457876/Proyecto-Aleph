"""IP → ASN, prefijo y organización titular; ASN → prefijos anunciados. Fuente: RIPEstat Data API.

Documentación oficial en la que se basa (https://stat.ripe.net/docs/02.data-api/):
- network-info: /data/network-info/data.json?resource=<ip> → data.asns, data.prefix.
- prefix-overview: /data/prefix-overview/data.json?resource=<prefijo> → data.asns[].asn/.holder,
  data.announced (la documentación lo describe como "True"/"False"), data.block.
- announced-prefixes: /data/announced-prefixes/data.json?resource=<número de ASN> → data.prefixes[].prefix.
- as-overview: /data/as-overview/data.json?resource=AS<n> → data.holder, data.announced.
- rir-stats-country: /data/rir-stats-country/data.json?resource=<ip> → país de registro según estadísticas RIR.
  La página de la API no documenta los campos de respuesta: el parser solo acepta data.located_resources[].location
  con un código de dos letras y, si no lo halla, lo declara en warnings. Verificar con una consulta real.

Todos los endpoints son de solo lectura y no requieren clave. Las IP no públicas (privadas, reservadas,
CGNAT) no se consultan: no tienen datos de enrutamiento públicos.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from ._http import get_json, open_client
from .errors import InvalidInputError, UpstreamError

RIPESTAT_BASE = "https://stat.ripe.net/data"
MAX_ASN = 4294967295
_ASN_RE = re.compile(r"(?:AS)?([0-9]{1,10})", re.IGNORECASE)
_COUNTRY_RE = re.compile(r"[A-Z]{2}")


class AsnInfo(BaseModel):
    asn: int
    holder: str = ""


class IpAsnResult(BaseModel):
    ip: str
    routed: bool
    prefix: str = ""
    asns: list[AsnInfo] = Field(default_factory=list)
    announced: bool | None = None
    country: str = ""  # país de registro según estadísticas RIR; no es geolocalización
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class AsnPrefixesResult(BaseModel):
    asn: int
    holder: str = ""
    announced: bool | None = None
    prefixes: list[str] = Field(default_factory=list)
    ipv4_count: int = 0
    ipv6_count: int = 0
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


def parse_asn(value: str | int) -> int:
    """Acepta 3333, 'AS3333' o 'as3333'. Lanza InvalidInputError si no es un número de ASN válido."""
    text = str(value).strip()
    match = _ASN_RE.fullmatch(text)
    if not match:
        raise InvalidInputError("ASN inválido: se espera un número, por ejemplo AS3333")
    number = int(match.group(1))
    if not 1 <= number <= MAX_ASN:
        raise InvalidInputError("ASN fuera de rango")
    return number


def _as_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= MAX_ASN else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value)
        return number if 0 < number <= MAX_ASN else None
    return None


def _envelope(payload: Any, url: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UpstreamError("respuesta de RIPEstat inesperada", url=url)
    if payload.get("status", "ok") != "ok":
        raise UpstreamError(f"RIPEstat devolvió estado {payload.get('status')!r}", url=url)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise UpstreamError("RIPEstat no devolvió el bloque data", url=url)
    return data


def _announced(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _country(payload: dict[str, Any]) -> str:
    located = payload.get("located_resources")
    if isinstance(located, list) and located and isinstance(located[0], dict):
        code = str(located[0].get("location", "")).strip().upper()
        if _COUNTRY_RE.fullmatch(code):
            return code
    return ""


async def lookup_ip(ip: str, *, client: httpx.AsyncClient | None = None) -> IpAsnResult:
    """ASN, prefijo, titular y país de registro de una IP pública."""
    try:
        address = ipaddress.ip_address(str(ip).strip())
    except ValueError as exc:
        raise InvalidInputError("dirección IP inválida") from exc
    text = str(address)
    if not address.is_global:
        return IpAsnResult(
            ip=text, routed=False,
            warnings=["IP no pública (privada, reservada, de loopback o CGNAT): no hay datos de enrutamiento"],
        )

    warnings: list[str] = []
    raw: dict[str, Any] = {}
    async with open_client(client) as http:
        info_url = f"{RIPESTAT_BASE}/network-info/data.json"
        info = _envelope(await get_json(http, info_url, params={"resource": text}), info_url)
        raw["network_info"] = info
        prefix = str(info.get("prefix") or "")
        asn_numbers = [n for n in (_as_number(v) for v in info.get("asns") or []) if n is not None]

        announced: bool | None = None
        holders: dict[int, str] = {}
        if prefix:
            overview_url = f"{RIPESTAT_BASE}/prefix-overview/data.json"
            overview = _envelope(await get_json(http, overview_url, params={"resource": prefix}), overview_url)
            raw["prefix_overview"] = overview
            announced = _announced(overview.get("announced"))
            for entry in overview.get("asns") or []:
                if isinstance(entry, dict):
                    number = _as_number(entry.get("asn"))
                    if number is not None:
                        holders[number] = str(entry.get("holder") or "")
                        if number not in asn_numbers:
                            asn_numbers.append(number)
        else:
            warnings.append("la IP no aparece anunciada en RIPEstat (sin prefijo enrutado)")

        country_url = f"{RIPESTAT_BASE}/rir-stats-country/data.json"
        country = ""
        try:
            country_payload = _envelope(await get_json(http, country_url, params={"resource": text}), country_url)
        except UpstreamError as exc:  # el país es un dato complementario: no invalida el resto
            warnings.append(f"rir-stats-country no disponible: {exc}")
        else:
            raw["rir_stats_country"] = country_payload
            country = _country(country_payload)
            if not country:
                warnings.append("rir-stats-country no devolvió un país reconocible (estructura no verificada)")

    asns = [AsnInfo(asn=n, holder=holders.get(n, "")) for n in asn_numbers]
    entities = [EntityRecord(
        type="ip", label=text, ref="ip", confidence=1.0,
        props={"prefix": prefix, "asn": asn_numbers, "announced": announced, "country_rir": country},
    )]
    relations: list[RelationRecord] = []
    for index, info_item in enumerate(asns):
        if info_item.holder:
            ref = f"org{index}"
            entities.append(EntityRecord(
                type="organization", label=info_item.holder, ref=ref, confidence=1.0,
                props={"asn": info_item.asn},
            ))
            relations.append(RelationRecord(src_ref="ip", dst_ref=ref, type="announced_by", confidence=1.0,
                                            props={"asn": info_item.asn}))
    if country:
        entities.append(EntityRecord(
            type="location", label=country, ref="country", confidence=0.8,
            props={"tipo": "país de registro RIR (no geolocalización)", "fuente": "RIPEstat rir-stats-country"},
        ))
        relations.append(RelationRecord(src_ref="ip", dst_ref="country", type="registered_in", confidence=0.8))
    return IpAsnResult(
        ip=text, routed=bool(prefix), prefix=prefix, asns=asns, announced=announced, country=country,
        entities=entities, relations=relations, warnings=warnings, raw=raw,
    )


async def announced_prefixes(asn: str | int, *, client: httpx.AsyncClient | None = None) -> AsnPrefixesResult:
    """Prefijos anunciados por un ASN, con el titular declarado."""
    number = parse_asn(asn)
    warnings: list[str] = []
    raw: dict[str, Any] = {}
    async with open_client(client) as http:
        prefixes_url = f"{RIPESTAT_BASE}/announced-prefixes/data.json"
        prefixes_data = _envelope(await get_json(http, prefixes_url, params={"resource": str(number)}),
                                  prefixes_url)
        raw["announced_prefixes"] = prefixes_data
        overview_url = f"{RIPESTAT_BASE}/as-overview/data.json"
        overview = _envelope(await get_json(http, overview_url, params={"resource": f"AS{number}"}), overview_url)
        raw["as_overview"] = overview

    prefixes: list[str] = []
    for item in prefixes_data.get("prefixes") or []:
        if isinstance(item, dict) and isinstance(item.get("prefix"), str):
            prefixes.append(item["prefix"])
    ipv4 = sum(1 for p in prefixes if ":" not in p)
    ipv6 = len(prefixes) - ipv4
    holder = str(overview.get("holder") or "")
    announced = _announced(overview.get("announced"))
    entities: list[EntityRecord] = []
    if holder:
        entities.append(EntityRecord(
            type="organization", label=holder, ref="holder", confidence=1.0,
            props={"asn": number, "prefijos_anunciados": len(prefixes)},
        ))
    else:
        warnings.append("el ASN no tiene titular registrado en RIPEstat")
    return AsnPrefixesResult(
        asn=number, holder=holder, announced=announced, prefixes=prefixes,
        ipv4_count=ipv4, ipv6_count=ipv6, entities=entities, warnings=warnings, raw=raw,
    )
