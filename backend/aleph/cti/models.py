"""Modelos del módulo CTI. Los contratos compartidos siguen en `aleph.core.schemas`."""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from aleph.core.schemas import EntityRecord, RelationRecord

from .tlp import TlpLevel, normalize_tlp

# Namespace propio de Aleph para UUIDv5 de SDO, SRO y objetos MISP. Los SCO usan el
# namespace estándar de STIX (lo calcula stix2), no este.
ALEPH_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:aleph:cti")

TECHNIQUE_ID_RE = re.compile(r"^T\d{4}(?:\.\d{3})?$")


def aleph_uuid(*parts: str) -> str:
    """UUIDv5 determinista a partir de partes de texto (misma entrada, mismo ID)."""
    return str(uuid.uuid5(ALEPH_NAMESPACE, "|".join(parts)))


class TechniqueScore(BaseModel):
    """Técnica ATT&CK asociada a un caso, con puntaje opcional y comentario de analista."""

    technique_id: str
    score: float | None = None  # 0..100; sin puntaje la técnica queda "unscored" en Navigator
    comment: str = ""

    @field_validator("technique_id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        text = value.strip().upper()
        if not TECHNIQUE_ID_RE.match(text):
            raise ValueError(f"ID ATT&CK con formato inválido: {value!r}")
        return text

    @field_validator("score")
    @classmethod
    def _score_range(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 100:
            raise ValueError("El puntaje ATT&CK debe estar entre 0 y 100.")
        return value


class CtiCase(BaseModel):
    """Caso listo para exportar. Todo lo que define el resultado va en el modelo (sin `now()`)."""

    case_id: str
    name: str
    description: str = ""
    tlp: TlpLevel = "amber"
    created_at: AwareDatetime  # fija created/modified de STIX y la fecha del evento MISP
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    techniques: list[TechniqueScore] = Field(default_factory=list)

    @field_validator("tlp", mode="before")
    @classmethod
    def _tlp(cls, value: Any) -> str:
        return normalize_tlp(value)
