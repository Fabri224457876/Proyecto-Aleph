"""STIX 2.1 con la librería stix2: exportación de un caso a Bundle e importación inversa.

Reglas de diseño:
- Determinismo: los IDs de SDO/SRO salen de UUIDv5 sobre (tipo, clave natural) con el namespace
  de Aleph; los SCO usan el cálculo estándar de stix2 (UUIDv5 con el namespace de STIX). Las
  fechas son las del caso (`created_at`). Exportar dos veces el mismo caso da el mismo JSON.
- Sin custom properties (`x_...`) sobre objetos estándar: `stix2.parse` las rechaza sin
  `allow_custom`. Los tipos sin equivalente estándar son SCO/SDO personalizados registrados
  (`x-aleph-*`), que sí se leen sin `allow_custom`.
- TLP 2.0 con las marcas oficiales de OASIS (ver `tlp.py`). No se usan TLP_WHITE/GREEN/...
  de stix2, que son TLP 1.0.
- Relaciones: se usa el tipo del vocabulario STIX solo cuando el par de tipos está permitido;
  si no, `related-to`, con la etiqueta `aleph-rel:<tipo>` para no perder el tipo original.
- `same_operator` (hipótesis de MENARD) es `related-to` con confidence, descripción y la
  etiqueta `aleph-rel:same_operator`. Nunca es una identidad confirmada.
- Un objeto solo se exporta si su TLP no supera el máximo pedido. Sus relaciones se caen con él,
  para no dejar referencias colgadas.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import stix2
from stix2 import v21
from stix2.exceptions import STIXError
from stix2.properties import StringProperty

from aleph.core.schemas import ENTITY_TYPES, EntityRecord, RelationRecord

from .attack import load_index
from .ioc import ip_scope
from .models import CtiCase, TechniqueScore, aleph_uuid
from .tlp import (
    TLP1_LEGACY_MARKINGS,
    TLP2_EXTENSION_ID,
    TLP2_MARKINGS,
    TlpLevel,
    can_include,
    most_restrictive,
    normalize_tlp,
    rank,
)

# ---------------------------------------------------------------------------
# Objetos personalizados (SCO y SDO) para tipos sin equivalente estándar.
# Se registran al importar el módulo para que stix2.parse los acepte sin allow_custom.
# ---------------------------------------------------------------------------


@v21.CustomObservable(
    "x-aleph-wallet",
    [
        ("currency", StringProperty(required=True)),
        ("address", StringProperty(required=True)),
    ],
    id_contrib_props=["currency", "address"],
)
class _WalletObservable:
    pass


@v21.CustomObservable(
    "x-aleph-phone", [("value", StringProperty(required=True))], id_contrib_props=["value"]
)
class _PhoneObservable:
    pass


@v21.CustomObservable(
    "x-aleph-document", [("value", StringProperty(required=True))], id_contrib_props=["value"]
)
class _DocumentObservable:
    pass


@v21.CustomObservable(
    "x-aleph-vehicle", [("value", StringProperty(required=True))], id_contrib_props=["value"]
)
class _VehicleObservable:
    pass


@v21.CustomObject(
    "x-aleph-event",
    [("name", StringProperty(required=True)), ("description", StringProperty())],
)
class _EventObject:
    pass


@v21.CustomObject(
    "x-aleph-place",
    [("name", StringProperty(required=True)), ("description", StringProperty())],
)
class _PlaceObject:
    pass


# ---------------------------------------------------------------------------
# Tablas de mapeo
# ---------------------------------------------------------------------------

# Tipo Aleph -> tipo de relación STIX deseado (antes de comprobar el par de tipos).
ALEPH_TO_STIX_REL: dict[str, str] = {
    "uses": "uses",
    "targets": "targets",
    "located_at": "located-at",
    "communicates_with": "communicates-with",
    "attributed_to": "attributed-to",
    "variant_of": "variant-of",
    "indicates": "indicates",
    "mitigates": "mitigates",
    "owns": "owns",
    "derived_from": "derived-from",
    "duplicate_of": "duplicate-of",
    "related_to": "related-to",
}
STIX_TO_ALEPH_REL: dict[str, str] = {v: k for k, v in ALEPH_TO_STIX_REL.items()}

# Pares (relación, tipo origen, tipo destino) donde el vocabulario STIX 2.1 se usa directamente.
# Lo demás cae a related-to. Ver la sección 4 de la especificación (tablas de relaciones).
_ALLOWED_PAIRS: frozenset[tuple[str, str, str]] = frozenset({
    ("uses", "threat-actor", "malware"),
    ("uses", "threat-actor", "tool"),
    ("uses", "threat-actor", "attack-pattern"),
    ("uses", "campaign", "malware"),
    ("uses", "campaign", "tool"),
    ("uses", "campaign", "attack-pattern"),
    ("targets", "malware", "identity"),
    ("targets", "malware", "vulnerability"),
    ("targets", "malware", "location"),
    ("located-at", "identity", "location"),
    ("communicates-with", "malware", "ipv4-addr"),
    ("communicates-with", "malware", "ipv6-addr"),
    ("communicates-with", "malware", "domain-name"),
    ("communicates-with", "malware", "url"),
})

HYPOTHESIS_RELATION = "same_operator"
HYPOTHESIS_DESCRIPTION = (
    "Hipótesis de mismo operador (MENARD): no es una identidad confirmada; "
    "requiere revisión humana."
)
_LABEL_REL = "aleph-rel:"
_LABEL_SCORE = "aleph-score:"

_HASH_ALGORITHMS = {"md5": "MD5", "sha1": "SHA-1", "sha256": "SHA-256"}
_HASH_PREFERENCE = ("SHA-256", "SHA-1", "MD5")
_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)
_PATTERN_RE = re.compile(r"^\[\s*([a-z0-9-]+):([^=\s]+)\s*=\s*'((?:\\.|[^'\\])*)'\s*\]$")
_GEO_KEYS = ("country", "region", "latitude", "longitude")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _clean(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v not in (None, "", [], {})}


def _tlp_marking(level: TlpLevel) -> v21.MarkingDefinition:
    marking = TLP2_MARKINGS[level]
    return v21.MarkingDefinition(
        id=marking.id,
        created=marking.created,
        name=marking.name,
        extensions={TLP2_EXTENSION_ID: {"extension_type": "property-extension", "tlp_2_0": level}},
    )


def _tlp_extension_definition() -> v21.ExtensionDefinition:
    # Copia del objeto oficial de OASIS (extension-definition-specifications/tlp-2.0).
    return v21.ExtensionDefinition(
        id=TLP2_EXTENSION_ID,
        name="TLP 2.0",
        description="This defines TLP 2.0 as a STIX extension",
        created="2022-10-01T00:00:00.000Z",
        modified="2022-10-01T00:00:00.000Z",
        created_by_ref="identity--b3bca3c2-1f3d-4b54-b44f-dac42c3a8f01",
        schema=(
            "https://github.com/oasis-open/cti-stix-common-objects/tree/master/"
            "extension-definition-specifications/tlp-2.0"
        ),
        version="1.0.0",
        extension_types=["property-extension"],
    )


# ---------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StixExport:
    # None si no quedó ningún objeto exportable (un bundle sin `objects` no es válido STIX).
    bundle: v21.Bundle | None
    skipped: tuple[str, ...] = field(default_factory=tuple)
    # True si el bundle contiene objetos personalizados `x-aleph-*`. stix2 exige
    # allow_custom=True para leer referencias a ellos; sin esos objetos alcanza con stix2.parse.
    requires_allow_custom: bool = False

    def to_json(self) -> str:
        if self.bundle is None:
            raise ValueError("No hay objetos exportables con los criterios indicados.")
        return self.bundle.serialize(pretty=True, sort_keys=True)


class _Exporter:
    def __init__(self, case: CtiCase, max_tlp: TlpLevel | None):
        self.case = case
        self.max_tlp = max_tlp
        self.created: datetime = case.created_at
        self.skipped: list[str] = []
        self.objects: dict[str, Any] = {}
        self.ref_to_id: dict[str, str] = {}
        self.ref_level: dict[str, TlpLevel] = {}
        self.ref_stix_type: dict[str, str] = {}
        self.uses_custom = False

    # -- utilidades ---------------------------------------------------------

    def _allowed(self, level: TlpLevel) -> bool:
        return self.max_tlp is None or can_include(level, self.max_tlp)

    def _marking(self, level: TlpLevel) -> str:
        return TLP2_MARKINGS[level].id

    def _sdo(self, stix_type: str, key: str, level: TlpLevel, **kwargs: Any) -> dict[str, Any]:
        return _clean({
            "id": f"{stix_type}--{aleph_uuid(stix_type, key)}",
            "created": self.created,
            "modified": self.created,
            "object_marking_refs": [self._marking(level)],
            **kwargs,
        })

    def _sco(self, level: TlpLevel, **kwargs: Any) -> dict[str, Any]:
        return _clean({"object_marking_refs": [self._marking(level)], **kwargs})

    def _entity_level(self, entity: EntityRecord) -> TlpLevel:
        raw = entity.props.get("tlp")
        return normalize_tlp(raw) if raw else self.case.tlp

    def _add(self, obj: Any) -> str:
        """Agrega un objeto STIX (sin duplicar IDs) y devuelve su ID."""
        if obj.id not in self.objects:
            self.objects[obj.id] = obj
            if obj.type.startswith("x-"):
                self.uses_custom = True
        return obj.id

    # -- entidades ----------------------------------------------------------

    def _convert_entity(self, entity: EntityRecord, level: TlpLevel) -> Any:
        t = entity.type
        p = entity.props
        label = entity.label.strip()
        desc = str(p.get("description", "") or "")
        key = _norm(label)

        if t == "person":
            if p.get("threat_actor") is True:
                return v21.ThreatActor(**self._sdo("threat-actor", key, level, name=label,
                                                  description=desc))
            return v21.Identity(**self._sdo("identity", f"individual|{key}", level, name=label,
                                            identity_class="individual", description=desc))
        if t == "organization":
            return v21.Identity(**self._sdo("identity", f"organization|{key}", level, name=label,
                                            identity_class="organization", description=desc))
        if t == "account":
            handle = str(p.get("handle") or label).lstrip("@")
            return v21.UserAccount(**self._sco(
                level, account_login=handle, account_type=str(p.get("platform", "")) or None,
                user_id=str(p.get("platform_uid") or handle), display_name=p.get("display_name")))
        if t == "email":
            return v21.EmailAddress(**self._sco(level, value=str(p.get("value") or label)))
        if t == "domain":
            return v21.DomainName(**self._sco(level, value=str(p.get("value") or label).lower()))
        if t == "ip":
            addr = ipaddress.ip_address(str(p.get("value") or label))
            cls = v21.IPv4Address if addr.version == 4 else v21.IPv6Address
            return cls(**self._sco(level, value=str(addr)))
        if t == "url":
            return v21.URL(**self._sco(level, value=str(p.get("value") or label)))
        if t == "hash":
            algorithm = _HASH_ALGORITHMS.get(str(p.get("algorithm", "")).lower().replace("-", ""))
            value = str(p.get("value") or label).lower()
            if algorithm is None:
                algorithm = {32: "MD5", 40: "SHA-1", 64: "SHA-256"}[len(value)]
            return v21.File(**self._sco(level, hashes={algorithm: value}))
        if t == "malware":
            return v21.Malware(**self._sdo("malware", key, level, name=label, description=desc,
                                           is_family=bool(p.get("is_family", True))))
        if t == "vulnerability":
            cve = str(p.get("cve_id") or (label if _CVE_RE.match(label) else "")).upper()
            return v21.Vulnerability(**self._sdo(
                "vulnerability", cve or key, level, name=cve or label, description=desc,
                external_references=[{"source_name": "cve", "external_id": cve}] if cve else None))
        if t == "location":
            if any(p.get(k) not in (None, "") for k in _GEO_KEYS):
                return v21.Location(**self._sdo(
                    "location", key, level, name=label, description=desc,
                    country=p.get("country"), region=p.get("region"),
                    latitude=p.get("latitude"), longitude=p.get("longitude"),
                    city=p.get("city")))
            return _PlaceObject(**self._sdo("x-aleph-place", key, level, name=label,
                                            description=desc))
        if t == "event":
            return _EventObject(**self._sdo("x-aleph-event", key, level, name=label,
                                            description=desc))
        if t == "wallet":
            address = str(p.get("address") or label)
            currency = str(p.get("currency") or "").upper() or _guess_currency(address)
            if currency is None:
                raise ValueError("no se pudo determinar la moneda de la billetera")
            return _WalletObservable(**self._sco(level, currency=currency, address=address))
        if t == "phone":
            return _PhoneObservable(**self._sco(level, value=label))
        if t == "document":
            return _DocumentObservable(**self._sco(level, value=label))
        if t == "vehicle":
            return _VehicleObservable(**self._sco(level, value=label))
        raise ValueError(f"tipo de entidad sin mapeo STIX: {t}")  # pragma: no cover

    def _indicator(self, entity: EntityRecord, level: TlpLevel) -> Any | None:
        if entity.props.get("malicious") is not True:
            return None
        pattern = _pattern_for(entity)
        if pattern is None:
            self.skipped.append(f"{entity.label}: sin patrón STIX para el tipo {entity.type}")
            return None
        if entity.type == "ip" and entity.props.get("routable") is False:
            self.skipped.append(f"{entity.label}: IP no ruteable, no se crea indicador")
            return None
        key = pattern
        return v21.Indicator(**self._sdo(
            "indicator", key, level, name=entity.label.strip(), pattern=pattern,
            pattern_type="stix", valid_from=self.created, indicator_types=["malicious-activity"]))

    # -- pasos del caso -----------------------------------------------------

    def run(self) -> StixExport:
        refs = [e.ref or f"entity-{i}" for i, e in enumerate(self.case.entities)]
        if len(set(refs)) != len(refs):
            raise ValueError("Hay refs de entidad repetidos en el caso.")
        pairs = sorted(zip(refs, self.case.entities, strict=True),
                       key=lambda pair: (pair[1].type, pair[1].label, pair[0]))
        for ref, entity in pairs:
            if entity.type not in ENTITY_TYPES:
                raise ValueError(f"Tipo de entidad desconocido: {entity.type!r}")
            level = self._entity_level(entity)
            if not self._allowed(level):
                self.skipped.append(f"{ref} ({entity.type}): TLP {level} supera el máximo")
                continue
            try:
                obj = self._convert_entity(entity, level)
            except (ValueError, STIXError) as exc:
                self.skipped.append(f"{ref} ({entity.type}): {exc}")
                continue
            stix_id = self._add(obj)
            self.ref_to_id[ref] = stix_id
            self.ref_level[ref] = level
            self.ref_stix_type[ref] = obj.type
            indicator = self._indicator(entity, level)
            if indicator is not None:
                self._add(indicator)

        self._relations()
        self._techniques()
        self._report()
        return self._assemble()

    def _relations(self) -> None:
        known = {e.ref or f"entity-{i}" for i, e in enumerate(self.case.entities)}
        for rel in sorted(self.case.relations, key=lambda r: (r.src_ref, r.dst_ref, r.type)):
            if rel.src_ref not in known or rel.dst_ref not in known:
                raise ValueError(f"Relación con referencia inexistente: {rel.src_ref} -> "
                                 f"{rel.dst_ref}")
            if rel.src_ref not in self.ref_to_id or rel.dst_ref not in self.ref_to_id:
                self.skipped.append(f"relación {rel.type} {rel.src_ref}->{rel.dst_ref}: "
                                    "un extremo quedó fuera de la exportación")
                continue
            levels = [self.ref_level[rel.src_ref], self.ref_level[rel.dst_ref]]
            if rel.props.get("tlp"):
                levels.append(normalize_tlp(rel.props["tlp"]))
            level = most_restrictive(levels)
            if not self._allowed(level):
                self.skipped.append(f"relación {rel.type}: TLP {level} supera el máximo")
                continue
            self._add(self._relationship(rel, level))

    def _relationship(self, rel: RelationRecord, level: TlpLevel) -> Any:
        src_id = self.ref_to_id[rel.src_ref]
        dst_id = self.ref_to_id[rel.dst_ref]
        aleph_type = rel.type.strip()
        wanted = ALEPH_TO_STIX_REL.get(aleph_type, "related-to")
        pair = (wanted, self.ref_stix_type[rel.src_ref], self.ref_stix_type[rel.dst_ref])
        stix_type = wanted if wanted == "related-to" or pair in _ALLOWED_PAIRS else "related-to"
        labels: list[str] = []
        description = str(rel.props.get("description", "") or "")
        if stix_type != ALEPH_TO_STIX_REL.get(aleph_type):
            labels.append(f"{_LABEL_REL}{aleph_type}")
        if aleph_type == HYPOTHESIS_RELATION:
            description = description or HYPOTHESIS_DESCRIPTION
        confidence = max(0, min(100, round(rel.confidence * 100)))
        # allow_custom: el extremo puede ser un objeto x-aleph-* (ver StixExport).
        return v21.Relationship(allow_custom=True, **self._sdo(
            "relationship", f"{src_id}|{aleph_type}|{dst_id}", level,
            relationship_type=stix_type, source_ref=src_id, target_ref=dst_id,
            description=description, confidence=confidence, labels=labels or None))

    def _techniques(self) -> None:
        index = load_index()
        seen: set[str] = set()
        for score in sorted(self.case.techniques, key=lambda t: t.technique_id):
            if score.technique_id in seen:
                continue
            seen.add(score.technique_id)
            tech = index.get(score.technique_id)
            if tech is None:
                self.skipped.append(f"{score.technique_id}: no está en el índice ATT&CK")
                continue
            if not self._allowed(self.case.tlp):
                self.skipped.append(f"técnicas: TLP {self.case.tlp} supera el máximo")
                break
            labels = [f"{_LABEL_SCORE}{int(score.score)}"] if score.score is not None else []
            # El ID es el oficial de MITRE (attack-pattern--...), así cualquier consumidor
            # que ya tenga ATT&CK lo reconoce sin duplicar la técnica.
            self._add(v21.AttackPattern(**_clean({
                "id": tech.stix_id,
                "created": self.created,
                "modified": self.created,
                "name": tech.name,
                "description": score.comment,
                "labels": labels,
                "external_references": [{"source_name": "mitre-attack", "external_id": tech.id,
                                         "url": tech.url}],
                "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": p}
                                      for p in tech.tactics],
                "object_marking_refs": [self._marking(self.case.tlp)],
            })))

    def _report(self) -> None:
        if not self._allowed(self.case.tlp):
            self.skipped.append(f"informe del caso: TLP {self.case.tlp} supera el máximo")
            return
        members = sorted(self.objects)
        if not members:
            self.skipped.append("informe del caso: no hay objetos para referenciar")
            return
        # allow_custom: el informe referencia también a los objetos x-aleph-* exportados.
        self._add(v21.Report(allow_custom=True, **self._sdo(
            "report", self.case.case_id, self.case.tlp, name=self.case.name,
            description=self.case.description, published=self.created,
            report_types=["threat-report"], object_refs=members)))

    def _assemble(self) -> StixExport:
        objects = [self.objects[k] for k in sorted(self.objects)]
        # Solo se incluyen las marcas que de verdad referencia algún objeto exportado.
        used_ids = {ref for obj in objects for ref in obj.get("object_marking_refs", [])}
        levels = [level for level in sorted(TLP2_MARKINGS, key=rank)
                  if TLP2_MARKINGS[level].id in used_ids]
        if not objects:
            return StixExport(bundle=None, skipped=tuple(self.skipped))
        extras: list[Any] = []
        if levels:
            extras.append(_tlp_extension_definition())
            extras.extend(_tlp_marking(level) for level in levels)
        bundle = v21.Bundle(
            id=f"bundle--{aleph_uuid('bundle', self.case.case_id)}",
            objects=extras + objects,
            allow_custom=self.uses_custom,
        )
        return StixExport(bundle=bundle, skipped=tuple(self.skipped),
                          requires_allow_custom=self.uses_custom)


def _guess_currency(address: str) -> str | None:
    if address.lower().startswith("0x") and len(address) == 42:
        return "ETH"
    if address.lower().startswith("bc1") or address[:1] in ("1", "3"):
        return "BTC"
    return None


def _pattern_for(entity: EntityRecord) -> str | None:
    value = str(entity.props.get("value") or entity.label).strip()
    if entity.type == "ip":
        version = ipaddress.ip_address(value).version
        return f"[ipv{version}-addr:value = {_literal(value)}]"
    if entity.type == "domain":
        return f"[domain-name:value = {_literal(value.lower())}]"
    if entity.type == "url":
        return f"[url:value = {_literal(value)}]"
    if entity.type == "email":
        return f"[email-addr:value = {_literal(value)}]"
    if entity.type == "hash":
        algorithm = _HASH_ALGORITHMS.get(str(entity.props.get("algorithm", "")).lower()
                                         .replace("-", ""))
        if algorithm is None:
            algorithm = {32: "MD5", 40: "SHA-1", 64: "SHA-256"}.get(len(value))
        if algorithm is None:
            return None
        return f"[file:hashes.'{algorithm}' = {_literal(value.lower())}]"
    return None


def export_case(case: CtiCase, *, max_tlp: TlpLevel | str | None = None) -> StixExport:
    """Convierte un caso en un Bundle STIX 2.1 determinista.

    `max_tlp`: si se indica, solo se exportan objetos con TLP <= ese nivel. Los objetos que
    quedan afuera se listan en `skipped` con el motivo.
    """
    limit = normalize_tlp(max_tlp) if max_tlp else None
    return _Exporter(case, limit).run()


# ---------------------------------------------------------------------------
# Importación
# ---------------------------------------------------------------------------


@dataclass
class CaseMeta:
    name: str
    description: str
    tlp: TlpLevel | None  # None si el informe llegó sin marca TLP
    published: datetime | None = None


@dataclass
class ImportResult:
    case: CaseMeta | None = None
    entities: list[EntityRecord] = field(default_factory=list)
    relations: list[RelationRecord] = field(default_factory=list)
    techniques: list[TechniqueScore] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _as_bundle(data: str | bytes | dict[str, Any] | v21.Bundle) -> v21.Bundle:
    if isinstance(data, v21.Bundle):
        return data
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    if isinstance(data, dict):
        data = json.dumps(data)
    parsed = stix2.parse(data, allow_custom=True)
    if not isinstance(parsed, v21.Bundle):
        raise ValueError("El documento no es un bundle STIX.")
    return parsed


def _marking_levels(objects: Iterable[Any]) -> dict[str, TlpLevel]:
    levels: dict[str, TlpLevel] = {}
    for obj in objects:
        if obj.get("type") != "marking-definition":
            continue
        if obj.get("definition_type") == "tlp" and obj.get("definition"):
            levels[obj["id"]] = normalize_tlp(obj["definition"]["tlp"])
            continue
        ext = (obj.get("extensions") or {}).get(TLP2_EXTENSION_ID)
        if ext and ext.get("tlp_2_0"):
            levels[obj["id"]] = normalize_tlp(ext["tlp_2_0"])
            continue
        if obj["id"] in TLP1_LEGACY_MARKINGS:
            levels[obj["id"]] = TLP1_LEGACY_MARKINGS[obj["id"]]
    return levels


def _object_level(obj: Any, levels: dict[str, TlpLevel]) -> TlpLevel | None:
    found = [levels[ref] for ref in obj.get("object_marking_refs", []) if ref in levels]
    return most_restrictive(found) if found else None


def _sco_entity(obj: Any, level: TlpLevel | None) -> EntityRecord | None:
    """Convierte un SDO/SCO en EntityRecord. Devuelve None si el tipo no es una entidad."""
    t = obj["type"]
    props: dict[str, Any] = {}
    label = ""
    aleph_type = ""
    if t == "identity":
        is_person = obj.get("identity_class") == "individual"
        aleph_type = "person" if is_person else "organization"
        label = obj.get("name", "")
    elif t == "threat-actor":
        aleph_type, label = "person", obj.get("name", "")
        props["threat_actor"] = True
    elif t == "intrusion-set":
        aleph_type, label = "organization", obj.get("name", "")
        props["stix_type"] = "intrusion-set"
    elif t == "campaign":
        aleph_type, label = "event", obj.get("name", "")
        props["stix_type"] = "campaign"
    elif t == "malware":
        aleph_type, label = "malware", obj.get("name", "")
        props["is_family"] = bool(obj.get("is_family", False))
    elif t == "tool":
        aleph_type, label = "malware", obj.get("name", "")
        props["stix_type"] = "tool"
        props["is_family"] = False
    elif t == "vulnerability":
        aleph_type = "vulnerability"
        cve = next((r.get("external_id") for r in obj.get("external_references", [])
                    if r.get("source_name") == "cve"), None)
        label = cve or obj.get("name", "")
        if cve:
            props["cve_id"] = cve
    elif t == "location":
        aleph_type = "location"
        label = obj.get("name") or obj.get("city") or obj.get("country") or obj.get("region", "")
        for key in ("country", "region", "city", "latitude", "longitude"):
            if obj.get(key) is not None:
                props[key] = obj[key]
    elif t == "user-account":
        aleph_type = "account"
        handle = obj.get("account_login") or obj.get("display_name") or obj.get("user_id", "")
        label = handle
        props.update(handle=handle, platform=obj.get("account_type", ""),
                     platform_uid=obj.get("user_id", ""), display_name=obj.get("display_name", ""))
    elif t == "email-addr":
        aleph_type, label = "email", obj["value"]
        props["value"] = obj["value"]
    elif t == "domain-name":
        aleph_type, label = "domain", obj["value"]
        props["value"] = obj["value"]
    elif t in ("ipv4-addr", "ipv6-addr"):
        addr = ipaddress.ip_address(obj["value"])
        aleph_type, label = "ip", str(addr)
        props.update(value=str(addr), version=addr.version, scope=ip_scope(addr),
                     routable=ip_scope(addr) == "public")
    elif t == "url":
        aleph_type, label = "url", obj["value"]
        props["value"] = obj["value"]
    elif t == "file":
        hashes = obj.get("hashes") or {}
        present = [a for a in _HASH_PREFERENCE if a in hashes]
        if not present:
            return None
        algorithm = present[0]
        label = hashes[algorithm]
        aleph_type = "hash"
        props.update(algorithm=algorithm, value=label, hashes=dict(hashes))
    elif t == "x-aleph-wallet":
        aleph_type, label = "wallet", obj["address"]
        props.update(currency=obj["currency"], address=obj["address"])
    elif t in ("x-aleph-phone", "x-aleph-document", "x-aleph-vehicle"):
        aleph_type = t.removeprefix("x-aleph-")
        label = obj["value"]
        props["value"] = obj["value"]
    elif t == "x-aleph-event":
        aleph_type, label = "event", obj.get("name", "")
    elif t == "x-aleph-place":
        aleph_type, label = "location", obj.get("name", "")
    else:
        return None
    if obj.get("description") and "description" not in props:
        props["description"] = obj["description"]
    if level is not None:
        props["tlp"] = level
    if aleph_type not in ENTITY_TYPES:  # pragma: no cover - el mapeo ya lo garantiza
        raise ValueError(f"tipo importado sin equivalente: {aleph_type}")
    confidence = obj.get("confidence", 100) / 100 if obj.get("confidence") is not None else 1.0
    return EntityRecord(type=aleph_type, label=str(label).strip(), props=props,
                        confidence=confidence, ref=obj["id"])


def import_bundle(data: str | bytes | dict[str, Any] | v21.Bundle) -> ImportResult:
    """Interpreta un bundle STIX 2.1 como entidades y relaciones de Aleph."""
    bundle = _as_bundle(data)
    objects = list(bundle.get("objects", []))
    result = ImportResult()
    levels = _marking_levels(objects)
    entity_ids: set[str] = set()
    by_value: dict[tuple[str, str], str] = {}

    for obj in objects:
        t = obj.get("type")
        if t in ("marking-definition", "extension-definition", "bundle", "relationship",
                 "indicator"):
            continue
        if t == "report":
            if result.case is not None:
                result.skipped.append(f"informe {obj['id']}: ya había un caso, se omite")
                continue
            published = obj.get("published")
            if published is not None and not isinstance(published, datetime):
                published = datetime.fromisoformat(str(published))
            result.case = CaseMeta(name=obj.get("name", ""),
                                   description=obj.get("description", "") or "",
                                   tlp=_object_level(obj, levels),
                                   published=published)
            continue
        if t == "attack-pattern":
            ref = next((r for r in obj.get("external_references", [])
                        if r.get("source_name") == "mitre-attack"), None)
            if ref is None:
                result.skipped.append(f"attack-pattern {obj['id']}: sin ID MITRE")
                continue
            score = next((int(lbl.removeprefix(_LABEL_SCORE)) for lbl in obj.get("labels", [])
                          if lbl.startswith(_LABEL_SCORE)), None)
            result.techniques.append(TechniqueScore(
                technique_id=ref["external_id"], score=score,
                comment=obj.get("description", "") or ""))
            continue
        if t == "grouping" or t in ("note", "opinion", "observed-data", "sighting", "report"):
            result.skipped.append(f"{t} {obj['id']}: no tiene equivalente de entidad")
            continue
        entity = _sco_entity(obj, _object_level(obj, levels))
        if entity is None:
            result.skipped.append(f"{t} {obj['id']}: tipo no soportado")
            continue
        if obj["id"] in entity_ids:
            continue
        entity_ids.add(obj["id"])
        result.entities.append(entity)
        by_value[(entity.type, _value_key(entity))] = entity.ref

    entity_by_ref = {e.ref: e for e in result.entities}
    for obj in objects:
        if obj.get("type") != "relationship":
            continue
        src, dst = obj.get("source_ref"), obj.get("target_ref")
        if src not in entity_by_ref or dst not in entity_by_ref:
            result.skipped.append(f"relación {obj['id']}: extremo no importado como entidad")
            continue
        stix_type = obj.get("relationship_type", "related-to")
        labels = obj.get("labels", []) or []
        aleph_type = next((lbl.removeprefix(_LABEL_REL) for lbl in labels
                           if lbl.startswith(_LABEL_REL)), None)
        if aleph_type is None:
            aleph_type = STIX_TO_ALEPH_REL.get(stix_type, stix_type)
        props: dict[str, Any] = {}
        if obj.get("description"):
            props["description"] = obj["description"]
        if stix_type != aleph_type and ALEPH_TO_STIX_REL.get(aleph_type) != stix_type:
            props["stix_relationship_type"] = stix_type
        confidence = obj.get("confidence", 100) / 100 if obj.get("confidence") is not None else 1.0
        result.relations.append(RelationRecord(src_ref=src, dst_ref=dst, type=aleph_type,
                                               props=props, confidence=confidence))

    for obj in objects:
        if obj.get("type") != "indicator":
            continue
        match = _PATTERN_RE.match(str(obj.get("pattern", "")).strip())
        if not match:
            result.skipped.append(f"indicador {obj['id']}: patrón no soportado")
            continue
        stix_type, path, raw = match.group(1), match.group(2), match.group(3)
        value = raw.replace("\\'", "'").replace("\\\\", "\\")
        if path.startswith("hashes."):
            kind, key = "hash", value.lower()
        else:
            kind = {"ipv4-addr": "ip", "ipv6-addr": "ip", "domain-name": "domain", "url": "url",
                    "email-addr": "email"}.get(stix_type, "")
            key = value.lower() if kind == "domain" else value
        ref = by_value.get((kind, key))
        if ref is None:
            result.skipped.append(f"indicador {obj['id']}: no hay objeto importado para {value}")
            continue
        entity_by_ref[ref].props["malicious"] = True

    return result


def _value_key(entity: EntityRecord) -> str:
    if entity.type == "domain":
        return entity.label.lower()
    if entity.type == "hash":
        return entity.label.lower()
    return entity.label
