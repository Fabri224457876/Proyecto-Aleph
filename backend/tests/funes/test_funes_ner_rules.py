"""Reglas determinísticas para documentos argentinos. Sin simular nada.

Los números son de ejemplo: válidos por dígito verificador, armados con secuencias obvias
(12345678, 11222333…) y no tomados de personas reales.
"""

import pytest

from aleph.core.schemas import ENTITY_TYPES
from aleph.funes.ar_rules import (
    cbu_check_digits,
    cuit_check_digit,
    find_dates,
    find_rule_hits,
    is_valid_cbu,
    is_valid_cuit,
    parse_local_date,
)
from aleph.funes.ner import extract_entities, extract_rule_entities

VALID_CBU = "2850590940090418135201"


def hits_of(text: str, rule: str):
    return [h for h in find_rule_hits(text)[0] if h.rule == rule]


def one(text: str, rule: str):
    found = hits_of(text, rule)
    assert len(found) == 1, [(h.rule, h.quote) for h in find_rule_hits(text)[0]]
    return found[0]


# --- CUIT / CUIL ---------------------------------------------------------------------------


@pytest.mark.parametrize(("first_ten", "digit"), [
    ("2012345678", 6), ("2733444555", 6), ("3071234567", 1), ("2311222333", 9),
    ("2099888777", 2),
])
def test_cuit_check_digit(first_ten, digit):
    assert cuit_check_digit(first_ten) == digit
    assert is_valid_cuit(f"{first_ten[:2]}-{first_ten[2:]}-{digit}")
    assert not is_valid_cuit(f"{first_ten}{(digit + 1) % 10}")


def test_cuit_check_digit_exhaustive_properties():
    """Para cada prefijo hay como mucho un verificador válido, y resto 1 no tiene ninguno."""
    without_digit = 0
    for body in range(10_000_000, 10_000_400):
        first_ten = f"20{body}"
        digit = cuit_check_digit(first_ten)
        valid = [d for d in range(10) if is_valid_cuit(f"{first_ten}{d}")]
        if digit is None:
            without_digit += 1
            assert valid == []
        else:
            assert valid == [digit]
    assert without_digit > 0  # el caso existe (por eso hay CUIT con prefijo 23/24)


def test_cuit_rejects_bad_shapes():
    assert not is_valid_cuit("20-1234567-6")  # corto
    assert not is_valid_cuit("21-12345678-6")  # prefijo inexistente
    with pytest.raises(ValueError):
        cuit_check_digit("abc")


def test_cuit_detected_with_formats_and_confidence():
    with_keyword = one("Titular: CUIT 20-12345678-6.", "cuit")
    assert with_keyword.label == "CUIT 20-12345678-6" and with_keyword.confidence == 0.99
    assert with_keyword.props["dni"] == "12345678" and with_keyword.props["tipo_persona"] == "fisica"
    assert with_keyword.quote == "20-12345678-6"
    assert one("factura de 30-71234567-1 por servicios", "cuit").props["tipo_persona"] == "juridica"
    assert one("cuil nro 27334445556", "cuit").props["kind"] == "cuil"
    bare = one("aparece 20123456786 en el listado", "cuit")
    assert bare.confidence == 0.8 and bare.type == "document"


def test_cuit_invalid_check_digit():
    # Sin palabra clave: once dígitos cualesquiera no son un CUIT.
    assert hits_of("ver 20-12345678-5 en el acta", "cuit") == []
    # Con palabra clave se informa, marcado y con confianza baja: puede ser un error de carga.
    flagged = one("CUIT 20-12345678-5", "cuit")
    assert flagged.props["verificador_valido"] is False and flagged.confidence == 0.3


def test_cuit_not_extracted_from_longer_numbers():
    assert hits_of("código 9920123456786123", "cuit") == []
    assert hits_of("ref 20-12345678-6-77", "cuit") == []


# --- DNI ---------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "DNI 12.345.678", "D.N.I. N° 12.345.678", "dni: 12345678", "DNI Nro. 12 345 678",
    "Documento Nacional de Identidad 12.345.678", "documento nro 12345678",
])
def test_dni_with_keyword(text):
    hit = one(text, "dni")
    assert hit.label == "DNI 12.345.678" and hit.value == "dni:12345678"
    assert hit.confidence == 0.95 and hit.type == "document"
    assert hit.quote == text and hit.start == 0


def test_dni_seven_digits_and_dotted_without_keyword():
    assert one("DNI 7.654.321", "dni").label == "DNI 7.654.321"
    dotted = one("se presentó con el 23.456.789 ante la mesa", "dni")
    assert dotted.confidence == 0.55 and dotted.props["con_palabra_clave"] is False


@pytest.mark.parametrize("text", [
    "cobró $ 12.345.678 en marzo", "por 12.345.678 pesos", "ciudad de 1.234.567 habitantes",
    "el número 12345678 suelto", "DNI 123", "versión 1.234.5678", "USD 12.345.678",
    "total 12.345.678,50",
])
def test_dni_false_positives(text):
    assert hits_of(text, "dni") == []


def test_dni_inside_cuit_is_not_reported_twice():
    hits, _ = find_rule_hits("CUIT 20-12345678-6")
    assert [h.rule for h in hits] == ["cuit"]


# --- CBU ---------------------------------------------------------------------------------


def test_cbu_check_digits():
    assert cbu_check_digits("2850590", "4009041813520") == (9, 1)
    assert is_valid_cbu(VALID_CBU)
    assert is_valid_cbu("28505909 40090418135201")
    assert not is_valid_cbu(VALID_CBU[:-1] + "2")  # segundo verificador
    assert not is_valid_cbu("28505908" + VALID_CBU[8:])  # primer verificador
    assert not is_valid_cbu("123")
    with pytest.raises(ValueError):
        cbu_check_digits("123", "456")


def test_cbu_single_digit_errors_are_always_caught():
    """El verificador detecta cualquier error de un solo dígito."""
    for position in range(22):
        for replacement in "0123456789":
            if replacement == VALID_CBU[position]:
                continue
            altered = VALID_CBU[:position] + replacement + VALID_CBU[position + 1:]
            assert not is_valid_cbu(altered), altered


def test_cbu_detection():
    hit = one(f"Transferir al CBU {VALID_CBU} a nombre de la cooperativa.", "cbu")
    assert hit.type == "document" and hit.props["kind"] == "cbu" and hit.confidence == 0.99
    assert hit.props["entidad"] == "285" and hit.props["verificador_valido"] is True
    assert one(f"cuenta {VALID_CBU}", "cbu").confidence == 0.95
    assert one(f"CVU: {VALID_CBU}", "cbu").props["kind"] == "cvu"
    bad = VALID_CBU[:-1] + "9"
    assert hits_of(f"número {bad}", "cbu") == []
    assert one(f"CBU {bad}", "cbu").props["verificador_valido"] is False


def test_cbu_digits_are_not_also_phone_or_cuit():
    hits, _ = find_rule_hits(f"CBU {VALID_CBU}")
    assert [h.rule for h in hits] == ["cbu"]


# --- Patentes ----------------------------------------------------------------------------


def test_plates_old_and_mercosur():
    old = one("circulaba en un Gol gris ABC123 por la colectora", "patente")
    assert (old.label, old.props["formato"], old.type, old.confidence) == (
        "ABC123", "vieja", "vehicle", 0.75)
    new = one("se secuestró el vehículo AB123CD", "patente")
    assert (new.label, new.props["formato"], new.confidence) == ("AB123CD", "mercosur", 0.85)
    spaced = one("patente AB 123 CD", "patente")
    assert spaced.label == "AB123CD" and spaced.confidence == 0.95 and spaced.quote == "AB 123 CD"
    assert one("dominio colocado ABC-123", "patente").label == "ABC123"
    assert one("vio un auto XYZ 987 estacionado", "patente").confidence == 0.5


@pytest.mark.parametrize("text", [
    "según la LEY 123 vigente", "DNI 123", "ART 245 del código", "abc123", "ABCD123", "AB1234CD",
    "ABC1234", "XABC123", "modelo ABC123X", "TEL 444",
])
def test_plate_false_positives(text):
    assert hits_of(text, "patente") == []


def test_plate_stopword_allowed_only_right_after_keyword():
    assert one("patente LEY 123", "patente").label == "LEY123"
    assert hits_of("las patentes que cita la LEY 123", "patente") == []


# --- Teléfonos ---------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "e164", "mobile"), [
    ("+54 9 11 4321-5678", "+5491143215678", True),
    ("+54 11 4321 5678", "+541143215678", False),
    ("011 4321-5678", "+541143215678", False),
    ("011 15 4321 5678", "+5491143215678", True),
    ("(011) 4321-5678", "+541143215678", False),
    ("(0351) 456-7890", "+543514567890", False),
    ("0341 15 555 1234", "+5493415551234", True),
    ("02944 42-1234", "+542944421234", False),
    ("5491143215678", "+5491143215678", True),
    ("+54 9 2944 421234", "+5492944421234", True),
])
def test_phone_formats(text, e164, mobile):
    hit = one(f"Llamar al {text} por la tarde", "telefono")
    assert hit.label == e164 and hit.props["movil"] is mobile and hit.type == "phone"
    assert hit.quote == text and hit.confidence == 0.9


def test_phone_same_number_same_key_across_formats():
    a = one("+54 9 11 4321-5678", "telefono")
    b = one("011 15 4321 5678", "telefono")
    c = one("tel 11 4321 5678", "telefono")
    assert a.value == b.value == c.value == "tel:1143215678"


def test_phone_confidence_depends_on_context():
    assert one("cel: 11 4321 5678", "telefono").confidence == 0.9
    assert one("anotó 11 4321 5678 en un papel", "telefono").confidence == 0.5


@pytest.mark.parametrize("text", [
    "1143215678", "expediente 4321-5678", "99 4321 5678", "el 12.345.678", "20-12345678-6",
    "011 4321-56789", "cuenta 1234567890123", "3,14159265358",
])
def test_phone_false_positives(text):
    assert hits_of(text, "telefono") == []


# --- Emails y URLs -----------------------------------------------------------------------


def test_email_and_url():
    text = ("Escribir a Juan.Perez+osint@Ejemplo.com.ar. Ver https://ejemplo.org/a/b?c=1, "
            "(http://sub.sitio.com.ar/x) y www.ejemplo.net.")
    email = one(text, "email")
    assert email.label == "juan.perez+osint@ejemplo.com.ar" and email.props["dominio"] == "ejemplo.com.ar"
    assert email.quote == "Juan.Perez+osint@Ejemplo.com.ar"
    urls = hits_of(text, "url")
    assert [u.label for u in urls] == ["https://ejemplo.org/a/b?c=1", "http://sub.sitio.com.ar/x",
                                       "http://www.ejemplo.net"]
    assert [u.props["host"] for u in urls] == ["ejemplo.org", "sub.sitio.com.ar", "www.ejemplo.net"]
    for hit in [email, *urls]:
        assert text[hit.start:hit.end] == hit.quote


def test_email_false_positives():
    assert hits_of("usuario@ sin dominio, @handle, a@b", "email") == []
    # Un email adentro de una URL no se informa dos veces
    hits, _ = find_rule_hits("https://sitio.com.ar/perfil?u=ana@ejemplo.com")
    assert [h.rule for h in hits] == ["url"]


# --- Fechas ------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "iso"), [
    ("10/03/2024", "2024-03-10"), ("3/4/2024", "2024-04-03"), ("10-03-2024", "2024-03-10"),
    ("10.03.2024", "2024-03-10"), ("2024-03-10", "2024-03-10"),
    ("2024-03-10T14:30", "2024-03-10T14:30"), ("2024-03-10 14:30:59", "2024-03-10T14:30"),
    ("12 de marzo de 2024", "2024-03-12"), ("1º de mayo del 2023", "2023-05-01"),
    ("3 de SETIEMBRE de 1998", "1998-09-03"), ("10/03/2024 a las 9:05", "2024-03-10T09:05"),
    ("10/03/2024 14.30", "2024-03-10T14:30"), ("12 de marzo de 2024 a las 14:30", "2024-03-12T14:30"),
    ("29/02/2024", "2024-02-29"),
])
def test_dates(text, iso):
    found = find_dates(f"ocurrió el {text} según el parte")
    assert [d.iso for d in found] == [iso]
    assert found[0].quote == text


def test_dates_day_first_and_two_digit_years():
    assert find_dates("05/03/24", two_digit_pivot=26)[0].iso == "2024-03-05"
    old = find_dates("05/03/87", two_digit_pivot=26)[0]
    assert old.iso == "1987-03-05" and old.two_digit_year


@pytest.mark.parametrize("text", [
    "31/02/2024", "29/02/2023", "10/13/2024", "0/5/2024", "32 de marzo de 2024", "1/2/3",
    "10/03/20245", "expediente 1234/2024", "12.345.678",
])
def test_date_false_positives(text):
    assert find_dates(text) == []


def test_invalid_date_does_not_swallow_the_next_one():
    assert [d.iso for d in find_dates("31/02/2024, 10.03.2024 y 11/03/2024")] == [
        "2024-03-10", "2024-03-11"]


def test_parse_local_date():
    assert parse_local_date("1990-05-17").date().isoformat() == "1990-05-17"
    assert parse_local_date("17/05/1990").date().isoformat() == "1990-05-17"
    assert parse_local_date("17 de mayo de 1990").date().isoformat() == "1990-05-17"
    assert parse_local_date("ayer") is None and parse_local_date("") is None


# --- Direcciones -------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "label"), [
    ("vive en Av. Corrientes 1234, cerca del Obelisco", "Av. Corrientes 1234"),
    ("domicilio: Avenida Hipólito Yrigoyen 850", "Avenida Hipólito Yrigoyen 850"),
    ("local de calle Florida 500", "calle Florida 500"),
    ("en Av. 9 de Julio 1000.", "Av. 9 de Julio 1000"),
    ("Calle 12 N° 345, La Plata", "Calle 12 N° 345"),
    ("Pje. San Lorenzo 380", "Pje. San Lorenzo 380"),
    ("Bv. Oroño 1500, piso 4, dpto B", "Bv. Oroño 1500, piso 4, dpto B"),
    ("Diagonal Norte 615 2° B", "Diagonal Norte 615 2° B"),
    ("Avda. General Paz al 3400", "Avda. General Paz al 3400"),
])
def test_addresses(text, label):
    hit = one(text, "direccion")
    assert hit.label == label and hit.type == "location" and hit.props["kind"] == "direccion"
    assert text[hit.start:hit.end] == hit.quote


@pytest.mark.parametrize("text", [
    "la calle estaba cortada desde las 1400", "Av. sin número", "caminó por la avenida 3 veces",
    "en la calle no había 20 personas",
])
def test_address_false_positives(text):
    assert hits_of(text, "direccion") == []


# --- Integración de reglas ----------------------------------------------------------------

DOCUMENT = (
    "El 12 de marzo de 2024 se identificó a un hombre con DNI 12.345.678 y CUIT 20-12345678-6, "
    "domiciliado en Av. Corrientes 1234. Usaba el celular 011 15 4321-5678 y el correo "
    "contacto@ejemplo.com.ar. El auto, patente AB123CD, figura en https://ejemplo.org/parte/42. "
    f"Los pagos iban al CBU {VALID_CBU}. Más tarde se lo llamó al +54 9 11 4321-5678."
)


def test_rule_entities_carry_quote_offset_and_method():
    entities, dates = extract_rule_entities(DOCUMENT)
    assert [d.iso for d in dates] == ["2024-03-12"]
    assert DOCUMENT[dates[0].offset:].startswith(dates[0].cita)
    kinds = [(e.type, e.props["regla"]) for e in entities]
    assert kinds == [("document", "dni"), ("document", "cuit"), ("location", "direccion"),
                     ("phone", "telefono"), ("email", "email"), ("vehicle", "patente"),
                     ("url", "url"), ("document", "cbu")]
    for entity in entities:
        props = entity.props
        assert entity.type in ENTITY_TYPES
        assert props["metodo"] == "regla" and props["estado"] == "propuesta"
        assert DOCUMENT[props["offset"]:props["offset"] + len(props["cita"])] == props["cita"]
        assert 0 < entity.confidence <= 1
    assert [e.ref for e in entities] == [f"e{i}" for i in range(1, 9)]


def test_same_phone_in_two_formats_is_one_entity():
    entities, _ = extract_rule_entities(DOCUMENT)
    phone = next(e for e in entities if e.type == "phone")
    assert phone.label == "+5491143215678"
    assert [m["cita"] for m in phone.props["menciones"]] == ["011 15 4321-5678", "+54 9 11 4321-5678"]
    assert phone.props["variantes"] == ["011 15 4321-5678", "+54 9 11 4321-5678"]


async def test_extract_entities_without_llm_uses_only_rules():
    result = await extract_entities(DOCUMENT, None)
    assert len(result.entities) == 8 and result.relations == [] and result.warnings == []
    assert result.model_dump() == (await extract_entities(DOCUMENT, None, use_llm=False)).model_dump()


def test_empty_text():
    assert extract_rule_entities("") == ([], [])
