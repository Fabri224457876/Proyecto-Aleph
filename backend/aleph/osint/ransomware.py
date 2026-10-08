"""Víctimas publicadas en sitios de filtración de grupos de ransomware (API pública de ransomware.live).

Documentación oficial en la que se basa: https://www.ransomware.live/apidocs
- Versión vigente: v2, base https://api.ransomware.live/v2. La v1 está marcada como obsoleta.
- Endpoints usados: GET /recentvictims, GET /countryvictims/{código ISO-2}, GET /groupvictims/{grupo}.
- Autenticación: la documentación de v2 dice que no requiere autenticación, con posible limitación de
  frecuencia. La versión PRO exige clave y no se usa aquí.
- Campos de víctima: la documentación los muestra como ejemplo (victim, group, attackdate, country, press,
  updates) y advierte que la lista puede no ser exhaustiva. Por eso solo se leen esos campos.
- Pendiente de confirmar: si el código de país va en mayúsculas o minúsculas. La documentación no lo fija;
  se envía en minúsculas.

Advertencia: son reivindicaciones de los propios grupos, no hechos verificados. Las entidades llevan confianza 0.5.
La identidad de una víctima es el nombre que publica el grupo.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from ._http import get_json, open_client
from .errors import InvalidInputError, UpstreamError

API_BASE = "https://api.ransomware.live/v2"
CLAIM_CONFIDENCE = 0.5
MAX_VICTIMS = 500
_COUNTRY_RE = re.compile(r"[A-Za-z]{2}")
_GROUP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,99}")


class RansomwareVictim(BaseModel):
    victim: str
    group: str
    attackdate: str = ""
    country: str = ""
    press: list[str] = Field(default_factory=list)


class VictimsReport(BaseModel):
    mode: str  # recent | country | group
    query: str
    victims: list[RansomwareVictim] = Field(default_factory=list)
    skipped: int = 0
    truncated: bool = False
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw: Any = None


def _press(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


async def list_victims(
    *,
    country: str | None = None,
    group: str | None = None,
    client: httpx.AsyncClient | None = None,
    max_items: int = MAX_VICTIMS,
) -> VictimsReport:
    """Víctimas recientes, por país (código ISO-2) o por grupo. Solo lectura."""
    if country and group:
        raise InvalidInputError("indique un país o un grupo, no ambos")
    if max_items < 1:
        raise InvalidInputError("max_items debe ser mayor que cero")
    if country is not None:
        code = country.strip()
        if not _COUNTRY_RE.fullmatch(code):
            raise InvalidInputError("código de país inválido: se esperan dos letras ISO-3166 (p. ej. AR)")
        url = f"{API_BASE}/countryvictims/{code.lower()}"
        mode, query = "country", f"país: {code.upper()}"
    elif group is not None:
        name = group.strip()
        if not _GROUP_RE.fullmatch(name):
            raise InvalidInputError("nombre de grupo inválido")
        url = f"{API_BASE}/groupvictims/{quote(name, safe='')}"
        mode, query = "group", f"grupo: {name}"
    else:
        url = f"{API_BASE}/recentvictims"
        mode, query = "recent", "víctimas recientes"

    async with open_client(client) as http:
        payload = await get_json(http, url)
    if not isinstance(payload, list):
        raise UpstreamError("respuesta inesperada de ransomware.live: se esperaba una lista", url=url)

    victims: list[RansomwareVictim] = []
    skipped = 0
    seen: set[tuple[str, str]] = set()
    for item in payload:
        if not isinstance(item, dict):
            skipped += 1
            continue
        victim_name = str(item.get("victim") or "").strip()
        group_name = str(item.get("group") or "").strip()
        if not victim_name or not group_name:
            skipped += 1
            continue
        key = (victim_name.lower(), group_name.lower())
        if key in seen:
            continue
        seen.add(key)
        victims.append(RansomwareVictim(
            victim=victim_name, group=group_name, attackdate=str(item.get("attackdate") or ""),
            country=str(item.get("country") or "").upper(), press=_press(item.get("press")),
        ))
    truncated = len(victims) > max_items
    victims = victims[:max_items]

    entities: list[EntityRecord] = []
    relations: list[RelationRecord] = []
    group_refs: dict[str, str] = {}
    for index, victim in enumerate(victims):
        ref = f"victim{index}"
        entities.append(EntityRecord(
            type="organization", label=victim.victim, ref=ref, confidence=CLAIM_CONFIDENCE,
            props={"country": victim.country, "attackdate": victim.attackdate,
                   "fuente": "ransomware.live (reivindicación del grupo)"},
        ))
        if victim.group not in group_refs:
            group_refs[victim.group] = f"group{len(group_refs)}"
            entities.append(EntityRecord(
                type="malware", label=victim.group, ref=group_refs[victim.group], confidence=CLAIM_CONFIDENCE,
                props={"tipo": "grupo de ransomware"},
            ))
        relations.append(RelationRecord(src_ref=ref, dst_ref=group_refs[victim.group], type="targeted_by",
                                        confidence=CLAIM_CONFIDENCE))
    warnings: list[str] = []
    if truncated:
        warnings.append(f"se devolvieron solo las primeras {max_items} víctimas")
    return VictimsReport(
        mode=mode, query=query, victims=victims, skipped=skipped, truncated=truncated,
        entities=entities, relations=relations, warnings=warnings, raw=payload,
    )
