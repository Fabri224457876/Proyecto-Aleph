"""Exportación de un caso como evento MISP (JSON).

Tipos y categorías por defecto según `describeTypes.json` de MISP (rama 2.5). Reglas:
- `to_ids` es True solo si la entidad está marcada como maliciosa (`props["malicious"]`).
  Un IOC no confirmado no debe llegar a un IDS.
- Cada atributo lleva su tag TLP (`tlp:amber+strict`, etc.). El evento lleva el más restrictivo
  de su contenido.
- Las técnicas ATT&CK van como tags de galaxia `misp-galaxy:mitre-attack-pattern="Nombre - ID"`.
- Las relaciones entre entidades no se exportan: MISP las modela con objetos, que quedan fuera
  de esta versión (se indica en `skipped`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from aleph.core.schemas import EntityRecord

from .attack import load_index
from .models import CtiCase, aleph_uuid
from .tlp import TlpLevel, can_include, misp_tag, most_restrictive, normalize_tlp

THREAT_LEVEL_UNDEFINED = "4"  # 1 alto, 2 medio, 3 bajo, 4 sin definir
ANALYSIS_ONGOING = "1"  # 0 inicial, 1 en curso, 2 completado
DISTRIBUTION_ORG_ONLY = "0"  # solo la organización
ATTRIBUTE_DISTRIBUTION_INHERIT = "5"  # heredar la del evento
_IDS_TYPES = {"ip-dst", "domain", "url", "md5", "sha1", "sha256", "btc", "email"}


@dataclass(frozen=True)
class MispExport:
    document: dict[str, Any]
    skipped: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> str:
        return json.dumps(self.document, ensure_ascii=False, indent=2, sort_keys=True)


def _attribute_spec(entity: EntityRecord) -> tuple[str, str, str, str]:
    """Devuelve (type, category, value, comment) para una entidad."""
    p = entity.props
    value = str(p.get("value") or entity.label).strip()
    t = entity.type
    if t == "ip":
        return "ip-dst", "Network activity", value, ""
    if t == "domain":
        return "domain", "Network activity", value.lower(), ""
    if t == "url":
        return "url", "Network activity", value, ""
    if t == "email":
        return "email", "Social network", value, ""
    if t == "hash":
        algorithm = str(p.get("algorithm", "")).upper().replace("-", "")
        misp_type = {"MD5": "md5", "SHA1": "sha1", "SHA256": "sha256"}.get(algorithm)
        if misp_type is None:
            misp_type = {32: "md5", 40: "sha1", 64: "sha256"}[len(value)]
        return misp_type, "Payload delivery", value.lower(), ""
    if t == "vulnerability":
        return "vulnerability", "External analysis", str(p.get("cve_id") or value).upper(), ""
    if t == "malware":
        return "malware-type", "Payload delivery", value, ""
    if t == "wallet":
        currency = str(p.get("currency", "")).upper()
        if currency == "BTC":
            return "btc", "Financial fraud", str(p.get("address") or value), ""
        return "other", "Financial fraud", str(p.get("address") or value), f"{currency} address"
    if t == "person":
        if p.get("threat_actor") is True:
            return "threat-actor", "Attribution", value, ""
        return "full-name", "Person", value, ""
    if t == "organization":
        return "text", "Other", value, "organización"
    if t == "account":
        platform = str(p.get("platform", "")) or "cuenta"
        return "other", "Social network", str(p.get("handle") or value), f"cuenta en {platform}"
    if t == "phone":
        return "phone-number", "Person", value, ""
    if t == "location":
        return "other", "Other", value, "lugar"
    if t == "event":
        return "text", "Other", value, "evento"
    if t == "document":
        return "other", "Other", value, "documento"
    if t == "vehicle":
        return "other", "Other", value, "vehículo"
    raise ValueError(f"tipo de entidad sin mapeo MISP: {t}")  # pragma: no cover


def _entity_level(entity: EntityRecord, case: CtiCase) -> TlpLevel:
    raw = entity.props.get("tlp")
    return normalize_tlp(raw) if raw else case.tlp


def export_misp(case: CtiCase, *, max_tlp: TlpLevel | str | None = None) -> MispExport:
    """Arma un evento MISP con los atributos del caso, respetando el TLP máximo."""
    limit = normalize_tlp(max_tlp) if max_tlp else None
    skipped: list[str] = []
    attributes: list[dict[str, Any]] = []
    levels: list[TlpLevel] = []

    ordered = sorted(
        enumerate(case.entities),
        key=lambda pair: (pair[1].type, pair[1].label, pair[1].ref or f"entity-{pair[0]}"),
    )
    for position, entity in ordered:
        ref = entity.ref or f"entity-{position}"
        level = _entity_level(entity, case)
        if limit is not None and not can_include(level, limit):
            skipped.append(f"{ref} ({entity.type}): TLP {level} supera el máximo")
            continue
        try:
            attr_type, category, value, comment = _attribute_spec(entity)
        except (ValueError, KeyError) as exc:
            skipped.append(f"{ref} ({entity.type}): {exc}")
            continue
        malicious = entity.props.get("malicious") is True
        attributes.append({
            "type": attr_type,
            "category": category,
            "value": value,
            "to_ids": malicious and attr_type in _IDS_TYPES,
            "comment": comment or str(entity.props.get("note", "") or ""),
            "distribution": ATTRIBUTE_DISTRIBUTION_INHERIT,
            "uuid": aleph_uuid("misp-attribute", f"{case.case_id}|{ref}"),
            "Tag": [{"name": misp_tag(level)}],
        })
        levels.append(level)

    info_visible = limit is None or can_include(case.tlp, limit)
    if not info_visible:
        skipped.append(f"metadatos del caso: TLP {case.tlp} supera el máximo")
    if info_visible and case.description:
        attributes.append({
            "type": "comment",
            "category": "Other",
            "value": case.description,
            "to_ids": False,
            "comment": "descripción del caso",
            "distribution": ATTRIBUTE_DISTRIBUTION_INHERIT,
            "uuid": aleph_uuid("misp-attribute", f"{case.case_id}|description"),
            "Tag": [{"name": misp_tag(case.tlp)}],
        })
        levels.append(case.tlp)
    if info_visible:
        levels.append(case.tlp)

    tags: list[dict[str, str]] = []
    index = load_index()
    if info_visible:
        seen: set[str] = set()
        for score in sorted(case.techniques, key=lambda t: t.technique_id):
            tech = index.get(score.technique_id)
            if tech is None or score.technique_id in seen:
                continue
            seen.add(score.technique_id)
            tags.append({"name": f'misp-galaxy:mitre-attack-pattern="{tech.name} - {tech.id}"'})
    event_level = most_restrictive(levels) if levels else (limit or case.tlp)
    tags.insert(0, {"name": misp_tag(event_level)})

    event = {
        "info": case.name if info_visible else "Caso con metadatos ocultos por TLP",
        "date": case.created_at.date().isoformat(),
        "threat_level_id": THREAT_LEVEL_UNDEFINED,
        "analysis": ANALYSIS_ONGOING,
        "distribution": DISTRIBUTION_ORG_ONLY,
        "published": False,
        "uuid": aleph_uuid("misp-event", case.case_id),
        "Tag": tags,
        "Attribute": attributes,
        "Object": [],
    }
    if case.relations:
        skipped.append("relaciones entre entidades: no se exportan a MISP en esta versión")
    return MispExport(document={"Event": event}, skipped=tuple(skipped))
