"""MITRE ATT&CK Enterprise: índice embebido, búsqueda y capas para ATT&CK Navigator.

El índice vive en `data/attack_enterprise.json` y lo regenera `build_attack_index.py` desde el
bundle oficial `mitre-attack/attack-stix-data`. No se consulta la red en tiempo de ejecución.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache
from importlib import resources
from typing import Any

from pydantic import BaseModel, ConfigDict

from .models import TECHNIQUE_ID_RE, TechniqueScore

LAYER_FORMAT_VERSION = "4.5"  # formato de capa de ATT&CK Navigator que se exporta
NAVIGATOR_VERSION = "4.9.0"  # mínimo que exige el formato 4.5
DEFAULT_GRADIENT = ("#ff6666", "#ffe766", "#8ec843")  # rojo -> amarillo -> verde


class Tactic(BaseModel):
    model_config = ConfigDict(frozen=True)

    shortname: str  # p. ej. "execution"; es lo que usa STIX en kill_chain_phases
    id: str  # p. ej. "TA0002"
    name: str
    url: str = ""


class Technique(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # "T1059" o subtécnica "T1059.001"
    name: str
    stix_id: str  # id STIX oficial, p. ej. "attack-pattern--…"
    tactics: tuple[str, ...]  # shortnames de tácticas
    parent: str | None = None  # id de la técnica padre si es subtécnica
    url: str = ""
    deprecated: bool = False

    @property
    def is_subtechnique(self) -> bool:
        return self.parent is not None


class AttackIndex:
    """Índice de tácticas y técnicas de una versión concreta de ATT&CK."""

    def __init__(self, data: dict[str, Any]):
        self.version: str = str(data["attack_version"])
        self.domain: str = str(data.get("domain", "enterprise-attack"))
        self.source: str = str(data.get("source", ""))
        self.counts: dict[str, int] = dict(data.get("counts", {}))
        self._tactics = {t["shortname"]: Tactic(**t) for t in data["tactics"]}
        self._techniques = {t["id"]: Technique(**t) for t in data["techniques"]}

    @classmethod
    def from_json(cls, text: str) -> AttackIndex:
        return cls(json.loads(text))

    def __len__(self) -> int:
        return len(self._techniques)

    def get(self, technique_id: str) -> Technique | None:
        """Busca por ID exacto (sin distinguir mayúsculas). Devuelve None si no existe."""
        return self._techniques.get(technique_id.strip().upper())

    def tactic(self, shortname: str) -> Tactic | None:
        return self._tactics.get(shortname)

    def tactics(self) -> list[Tactic]:
        return sorted(self._tactics.values(), key=lambda t: t.id)

    def techniques(self) -> list[Technique]:
        return sorted(self._techniques.values(), key=lambda t: _sort_key(t.id))

    def subtechniques(self, parent_id: str) -> list[Technique]:
        parent = parent_id.strip().upper()
        return [t for t in self.techniques() if t.parent == parent]

    def search(self, query: str, limit: int = 20) -> list[Technique]:
        """Búsqueda por ID (exacto o por prefijo) o por texto en el nombre o la táctica.

        Orden: coincidencia exacta del nombre, después prefijo del nombre, después todas las
        palabras presentes. Las coincidencias por táctica van al final.
        """
        q = query.strip()
        if not q:
            return []
        upper = q.upper()
        if TECHNIQUE_ID_RE.match(upper):
            exact = self.get(upper)
            prefix = [t for t in self.techniques() if t.id.startswith(upper + ".")]
            found = ([exact] if exact else []) + prefix
            return found[:limit]

        low = q.lower()
        tokens = low.split()
        scored: list[tuple[int, Technique]] = []
        for tech in self._techniques.values():
            name = tech.name.lower()
            if name == low:
                score = 100
            elif name.startswith(low):
                score = 80
            elif all(tok in name for tok in tokens):
                score = 60
            elif low in {self._tactic_label(s) for s in tech.tactics}:
                score = 40
            else:
                continue
            scored.append((score, tech))
        scored.sort(key=lambda pair: (-pair[0], _sort_key(pair[1].id)))
        return [tech for _, tech in scored[:limit]]

    def _tactic_label(self, shortname: str) -> str:
        tactic = self._tactics.get(shortname)
        return tactic.name.lower() if tactic else shortname.lower()


def _sort_key(technique_id: str) -> tuple[int, int]:
    base, _, sub = technique_id.partition(".")
    return int(base[1:]), int(sub or 0)


@lru_cache(maxsize=1)
def load_index() -> AttackIndex:
    """Carga el índice embebido una sola vez por proceso."""
    raw = resources.files("aleph.cti").joinpath("data/attack_enterprise.json").read_text(
        encoding="utf-8"
    )
    return AttackIndex.from_json(raw)


def navigator_layer(
    entries: Sequence[TechniqueScore],
    *,
    name: str,
    description: str = "",
    index: AttackIndex | None = None,
) -> dict[str, Any]:
    """Arma una capa de ATT&CK Navigator (formato 4.5) a partir de técnicas con puntaje.

    Falla con ValueError si una técnica no está en el índice, si se repite o si el nombre
    está vacío. No inventa técnicas: si el ID no existe en la versión embebida, se rechaza.
    """
    if not name.strip():
        raise ValueError("La capa necesita un nombre.")
    idx = index or load_index()

    unknown: list[str] = []
    seen: set[str] = set()
    techniques: list[dict[str, Any]] = []
    for entry in entries:
        tid = entry.technique_id
        if idx.get(tid) is None:
            unknown.append(tid)
            continue
        if tid in seen:
            raise ValueError(f"Técnica repetida en la capa: {tid}")
        seen.add(tid)
        item: dict[str, Any] = {
            "techniqueID": tid,
            "enabled": True,
            "color": "",
            "comment": entry.comment,
            "metadata": [],
            "links": [],
        }
        if entry.score is not None:
            item["score"] = int(entry.score) if float(entry.score).is_integer() else entry.score
        techniques.append(item)
    if unknown:
        raise ValueError(
            f"IDs ATT&CK inexistentes en la versión {idx.version}: {', '.join(unknown)}"
        )

    return {
        "name": name,
        "versions": {
            "attack": idx.version,
            "navigator": NAVIGATOR_VERSION,
            "layer": LAYER_FORMAT_VERSION,
        },
        "domain": idx.domain,
        "description": description,
        "sorting": 0,
        "hideDisabled": False,
        "techniques": techniques,
        "gradient": {"colors": list(DEFAULT_GRADIENT), "minValue": 0, "maxValue": 100},
        "legendItems": [],
        "metadata": [],
        "links": [],
        "showTacticRowBackground": False,
        "tacticRowBackground": "#dddddd",
        "selectTechniquesAcrossTactics": True,
        "selectSubtechniquesWithParent": False,
        "selectVisibleTechniques": False,
    }
