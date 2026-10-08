import pytest

from aleph.cti.tlp import (
    TLP2_EXTENSION_ID,
    TLP2_MARKINGS,
    can_include,
    compare,
    misp_tag,
    most_restrictive,
    normalize_tlp,
    rank,
    stix_marking_name,
)


def test_orden_de_restriccion_tlp2():
    assert compare("clear", "green") == -1
    assert compare("amber", "amber") == 0
    assert compare("red", "amber+strict") == 1
    assert [rank(x) for x in ["clear", "green", "amber", "amber+strict", "red"]] == [0, 1, 2, 3, 4]


def test_normalizacion_acepta_prefijo_mayusculas_y_white():
    assert normalize_tlp("TLP:AMBER+STRICT") == "amber+strict"
    assert normalize_tlp(" Green ") == "green"
    assert normalize_tlp("white") == "clear"  # TLP 1.0 llamaba WHITE a lo que hoy es CLEAR


def test_nivel_desconocido_falla():
    with pytest.raises(ValueError):
        normalize_tlp("purple")


@pytest.mark.parametrize(
    ("obj", "max_tlp", "expected"),
    [
        ("clear", "green", True),
        ("green", "green", True),
        ("amber", "green", False),
        ("amber+strict", "amber", False),
        ("red", "red", True),
        ("green", "clear", False),
    ],
)
def test_can_include(obj, max_tlp, expected):
    assert can_include(obj, max_tlp) is expected


def test_most_restrictive_y_lista_vacia():
    assert most_restrictive(["green", "amber+strict", "amber"]) == "amber+strict"
    with pytest.raises(ValueError):
        most_restrictive([])


def test_marcas_tlp2_coinciden_con_las_oficiales_de_oasis():
    # Valores publicados en oasis-open/cti-stix-common-objects (objects/marking-definition
    # y extension-definition-specifications/tlp-2.0/examples).
    expected = {
        "clear": "marking-definition--94868c89-83c2-464b-929b-a1a8aa3c8487",
        "green": "marking-definition--bab4a63c-aed9-4cf5-a766-dfca5abac2bb",
        "amber": "marking-definition--55d920b0-5e8b-4f79-9ee9-91f868d9b421",
        "amber+strict": "marking-definition--939a9414-2ddd-4d32-a0cd-375ea402b003",
        "red": "marking-definition--e828b379-4e03-4974-9ac4-e53a884c97c1",
    }
    assert {level: m.id for level, m in TLP2_MARKINGS.items()} == expected
    assert {m.created for m in TLP2_MARKINGS.values()} == {"2022-10-01T00:00:00.000Z"}
    assert TLP2_EXTENSION_ID == "extension-definition--60a3c5c5-0d10-413e-aab3-9e08dde9e88d"


def test_nombres_de_salida():
    assert stix_marking_name("amber+strict") == "TLP:AMBER+STRICT"
    assert misp_tag("clear") == "tlp:clear"
