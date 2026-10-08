"""Niveles TLP 2.0: orden de restricción, comparación y marcas STIX oficiales.

Orden de menor a mayor restricción: clear < green < amber < amber+strict < red.
Una exportación con TLP máximo `M` solo puede llevar objetos cuyo nivel sea <= M.

Las marcas STIX de TLP 2.0 se representan como `marking-definition` con una
extensión (`tlp_2_0`), no con `definition_type: "tlp"` (eso es TLP 1.0). Los IDs y
fechas de abajo son los publicados por OASIS en `cti-stix-common-objects`
(objects/marking-definition y extension-definition-specifications/tlp-2.0).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

TlpLevel = Literal["clear", "green", "amber", "amber+strict", "red"]
TLP_ORDER: tuple[TlpLevel, ...] = ("clear", "green", "amber", "amber+strict", "red")
_RANK: dict[str, int] = {level: i for i, level in enumerate(TLP_ORDER)}
# TLP 1.0 llamaba WHITE al nivel que TLP 2.0 llama CLEAR.
_LEGACY_ALIASES: dict[str, str] = {"white": "clear"}


def normalize_tlp(value: str) -> TlpLevel:
    """Devuelve el nivel TLP 2.0 canónico. Acepta 'TLP:AMBER+STRICT', mayúsculas y 'white'."""
    text = str(value).strip().lower()
    text = text.removeprefix("tlp:")
    text = _LEGACY_ALIASES.get(text, text)
    if text not in _RANK:
        raise ValueError(f"Nivel TLP desconocido: {value!r}")
    return text  # type: ignore[return-value]


def rank(level: str) -> int:
    """Posición de sensibilidad: 0 = CLEAR (menos restrictivo), 4 = RED (más restrictivo)."""
    return _RANK[normalize_tlp(level)]


def compare(a: str, b: str) -> int:
    """-1 si a es menos restrictivo que b, 0 si son iguales, 1 si es más restrictivo."""
    ra, rb = rank(a), rank(b)
    return (ra > rb) - (ra < rb)


def can_include(object_tlp: str, max_tlp: str) -> bool:
    """True si un objeto con `object_tlp` puede exportarse con un TLP máximo `max_tlp`."""
    return rank(object_tlp) <= rank(max_tlp)


def most_restrictive(levels: Iterable[str]) -> TlpLevel:
    """Nivel más restrictivo de una lista (p. ej. el de una relación entre dos objetos)."""
    normalized = [normalize_tlp(lvl) for lvl in levels]
    if not normalized:
        raise ValueError("No se puede calcular el TLP más restrictivo de una lista vacía.")
    return max(normalized, key=rank)


def stix_marking_name(level: str) -> str:
    """Nombre de la marca, p. ej. 'TLP:AMBER+STRICT'."""
    return f"TLP:{normalize_tlp(level).upper()}"


def misp_tag(level: str) -> str:
    """Etiqueta de la taxonomía 'tlp' de MISP, p. ej. 'tlp:amber+strict'."""
    return f"tlp:{normalize_tlp(level)}"


TLP2_EXTENSION_ID = "extension-definition--60a3c5c5-0d10-413e-aab3-9e08dde9e88d"


@dataclass(frozen=True)
class TlpMarking:
    level: TlpLevel
    id: str
    created: str
    name: str


TLP2_MARKINGS: dict[str, TlpMarking] = {
    "clear": TlpMarking(
        "clear", "marking-definition--94868c89-83c2-464b-929b-a1a8aa3c8487",
        "2022-10-01T00:00:00.000Z", "TLP:CLEAR",
    ),
    "green": TlpMarking(
        "green", "marking-definition--bab4a63c-aed9-4cf5-a766-dfca5abac2bb",
        "2022-10-01T00:00:00.000Z", "TLP:GREEN",
    ),
    "amber": TlpMarking(
        "amber", "marking-definition--55d920b0-5e8b-4f79-9ee9-91f868d9b421",
        "2022-10-01T00:00:00.000Z", "TLP:AMBER",
    ),
    "amber+strict": TlpMarking(
        "amber+strict", "marking-definition--939a9414-2ddd-4d32-a0cd-375ea402b003",
        "2022-10-01T00:00:00.000Z", "TLP:AMBER+STRICT",
    ),
    "red": TlpMarking(
        "red", "marking-definition--e828b379-4e03-4974-9ac4-e53a884c97c1",
        "2022-10-01T00:00:00.000Z", "TLP:RED",
    ),
}

# Marcas TLP 1.0 (definition_type "tlp"), solo para interpretar bundles antiguos al importar.
TLP1_LEGACY_MARKINGS: dict[str, TlpLevel] = {
    "marking-definition--613f2e26-407d-48c7-9eca-b8e91df99dc9": "clear",  # TLP:WHITE
    "marking-definition--34098fce-860f-48ae-8e50-ebd3cc5e41da": "green",
    "marking-definition--f88d31f6-486f-44da-b317-01333bde0b82": "amber",
    "marking-definition--5e57c739-391a-4eb3-b6be-7d15ca92d5ed": "red",
}
