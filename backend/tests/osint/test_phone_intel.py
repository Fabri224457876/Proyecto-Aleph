import pytest

from aleph.osint.phone_intel import analyze_phone


@pytest.mark.parametrize("raw,e164,line_type,area", [
    ("+54 9 11 2345-6789", "+5491123456789", "móvil", "11"),
    ("011 15 2345-6789", "+5491123456789", "móvil", "11"),
    ("(011) 4444-5555", "+541144445555", "fijo (inferido: sin marcador de celular)", "11"),
    ("+54 221 456-7890", "+542214567890", "fijo (inferido: sin marcador de celular)", "221"),
    ("+54 2966 12-3456", "+542966123456", "fijo (inferido: sin marcador de celular)", "2966"),
    ("00 54 11 1234 5678", "+541112345678", "fijo (inferido: sin marcador de celular)", "11"),
    ("+54 351 123-4567", "+543511234567", "fijo (inferido: sin marcador de celular)", "351"),
])
def test_argentine_numbers_normalize_and_locate(raw, e164, line_type, area):
    report = analyze_phone(raw)
    assert report.valid is True, report.error
    assert report.e164 == e164
    assert report.country_iso == "AR" and report.country_code == "54"
    assert report.line_type == line_type
    assert report.area_code == area


def test_argentine_mobile_from_national_format_has_area_and_locality():
    report = analyze_phone("011 15 2345-6789")
    assert report.national_number == "1123456789"
    assert report.area_name.startswith("Ciudad Autónoma")
    phone = next(e for e in report.entities if e.type == "phone")
    assert phone.props["line_type"] == "móvil"
    area = next(e for e in report.entities if e.type == "location" and e.ref == "area")
    assert area.confidence == pytest.approx(0.8)
    assert any(r.type == "located_in" and r.dst_ref == "area" for r in report.relations)


def test_unknown_argentine_area_is_flagged_not_invented():
    report = analyze_phone("+54 3471 56-7890")  # característica 3471 no está en la tabla embebida
    assert report.valid is True
    assert report.e164 == "+543471567890"
    assert report.area_code == "" and report.area_name == ""
    assert any("no incluida" in w for w in report.warnings)
    assert [r.dst_ref for r in report.relations] == ["country"]  # cae al nivel país, no inventa área


def test_toll_free_0800_is_identified():
    report = analyze_phone("0800-333-4444")
    assert report.valid is True
    assert report.line_type == "gratuito (0800)"
    assert report.e164 == "+548003334444"


@pytest.mark.parametrize("raw,country_iso,country_name_part", [
    ("+44 20 7946 0958", "GB", "Reino Unido"),
    ("+34 912 345 678", "ES", "España"),
    ("+1 202 555 0100", "", "NANP"),
    ("+55 11 91234-5678", "BR", "Brasil"),
])
def test_international_numbers_get_e164_and_country(raw, country_iso, country_name_part):
    report = analyze_phone(raw)
    assert report.valid is True, report.error
    assert report.e164 == "+" + "".join(ch for ch in raw if ch.isdigit())
    assert report.country_iso == country_iso
    assert country_name_part in report.country_name
    assert report.line_type == "indeterminado"
    assert report.area_code == ""


def test_international_numbers_produce_country_location_entity():
    report = analyze_phone("+34 912 345 678")
    location = next(e for e in report.entities if e.type == "location")
    assert location.label == "España"
    assert report.relations[0].type == "located_in" and report.relations[0].dst_ref == "country"


def test_unknown_country_code_keeps_e164_and_warns():
    report = analyze_phone("+882 1234 5678")  # 882 no está en la tabla embebida
    assert report.valid is True
    assert report.country_iso == ""
    assert any("no incluido" in w for w in report.warnings)


@pytest.mark.parametrize("raw", [
    "+54 11 123",  # demasiado corto para un número argentino
    "+54 11 1234 5678 9999",  # demasiado largo
    "abc",  # sin dígitos
    "+1 2a3",  # caracteres inválidos
    "",
])
def test_invalid_numbers_are_reported_without_exception(raw):
    if raw == "":
        from aleph.osint.errors import InvalidInputError

        with pytest.raises(InvalidInputError):
            analyze_phone(raw)
        return
    report = analyze_phone(raw)
    assert report.valid is False
    assert report.error


def test_national_number_without_country_code_needs_argentina_region():
    report = analyze_phone("11 1234 5678", default_region="US")
    assert report.valid is False
    assert "con +código de país" in report.error
    assert analyze_phone("11 1234 5678").valid is True  # por defecto, Argentina
