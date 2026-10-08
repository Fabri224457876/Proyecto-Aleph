"""Motor de contradicciones. La detección se prueba sin simular nada; el LLM solo en lo suyo."""

from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from aleph.funes.contradictions import (
    READINGS,
    Claim,
    ContradictionConfig,
    Place,
    available_travel_seconds,
    detect_contradictions,
    explain_contradictions,
    extract_claims,
    haversine_km,
    normalize_value,
)

BUENOS_AIRES = Place(name="Buenos Aires", lat=-34.6037, lon=-58.3816)
ROSARIO = Place(name="Rosario", lat=-32.9442, lon=-60.6505)
CORDOBA = Place(name="Córdoba", lat=-31.4201, lon=-64.1888)
MADRID = Place(name="Madrid", lat=40.4168, lon=-3.7038)
OBELISCO = Place(name="Obelisco", lat=-34.6037, lon=-58.3816)
CONGRESO = Place(name="Congreso", lat=-34.6098, lon=-58.3925)  # ~1,2 km del Obelisco


def at(year=2024, month=3, day=10, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def seen(claim_id, place, start, end=None, source="F1", subject="Juan Pérez", **kwargs):
    return Claim(id=claim_id, subject=subject, predicate="ubicado_en", value=place.name,
                 place=place, start=start, end=end, source=source, **kwargs)


def kinds(found):
    return [c.kind for c in found]


# --- Haversine -----------------------------------------------------------------------------


def test_haversine_known_distances():
    assert haversine_km(0, 0, 0, 0) == 0
    assert haversine_km(-34.6037, -58.3816, -32.9442, -60.6505) == pytest.approx(280, abs=5)
    assert haversine_km(-34.6037, -58.3816, 40.4168, -3.7038) == pytest.approx(10_050, abs=60)
    assert haversine_km(0, 0, 0, 180) == pytest.approx(20_015, abs=5)  # media circunferencia
    assert haversine_km(90, 0, -90, 0) == pytest.approx(20_015, abs=5)
    assert haversine_km(10, 20, 30, 40) == pytest.approx(haversine_km(30, 40, 10, 20))
    assert haversine_km(0, 179.5, 0, -179.5) == pytest.approx(111.2, abs=0.5)  # cruza el antimeridiano


def test_place_validates_coordinates():
    with pytest.raises(ValueError):
        Place(lat=91, lon=0)
    assert not Place(name="sin coordenadas").has_coords


# --- (a) Imposibilidad espacio-temporal ------------------------------------------------------


def test_same_person_two_cities_too_fast():
    claims = [seen("a", BUENOS_AIRES, at(hour=12), source="red-social"),
              seen("b", MADRID, at(hour=14), source="registro-hotel")]
    found = detect_contradictions(claims)
    assert kinds(found) == ["espacio_temporal"]
    c = found[0]
    assert c.id == "C1" and c.severity == "alta" and c.subject == "Juan Pérez"
    assert c.claim_ids == ["a", "b"] and c.sources == ["red-social", "registro-hotel"]
    assert c.details["distancia_km"] == pytest.approx(10_050, abs=60)
    assert c.details["tiempo_disponible_s"] == 7200
    assert c.details["velocidad_requerida_kmh"] == pytest.approx(5_025, abs=40)
    assert "Buenos Aires" in c.summary and "Madrid" in c.summary and "km/h" in c.summary


def test_feasible_trip_is_not_a_contradiction():
    # 280 km en 4 horas: se puede.
    assert detect_contradictions([seen("a", BUENOS_AIRES, at(hour=8)),
                                  seen("b", ROSARIO, at(hour=12))]) == []
    # Buenos Aires–Madrid en 13 horas, en avión: se puede.
    assert detect_contradictions([seen("a", BUENOS_AIRES, at(hour=0)),
                                  seen("b", MADRID, at(hour=13))]) == []


def test_max_speed_is_configurable():
    claims = [seen("a", BUENOS_AIRES, at(hour=10)), seen("b", ROSARIO, at(hour=11))]
    assert detect_contradictions(claims) == []  # 280 km/h: con avión alcanza
    by_car = detect_contradictions(claims, ContradictionConfig(max_speed_kmh=130))
    assert kinds(by_car) == ["espacio_temporal"] and by_car[0].severity == "alta"
    assert by_car[0].details["velocidad_maxima_kmh"] == 130


def test_severity_grades():
    config = ContradictionConfig(max_speed_kmh=100)
    def severity(minutes):
        found = detect_contradictions(
            [seen("a", BUENOS_AIRES, at(hour=0)),
             seen("b", ROSARIO, at(hour=0) + timedelta(minutes=minutes))], config)
        return found[0].severity if found else None
    assert severity(60) == "alta"  # exigiría 280 km/h con máximo 100
    assert severity(130) == "media"  # ~129 km/h
    assert severity(160) == "baja"  # ~105 km/h: apenas por encima
    assert severity(180) is None  # ~93 km/h


def test_simultaneous_presence_and_order_independence():
    a, b = seen("a", BUENOS_AIRES, at(hour=12)), seen("b", CORDOBA, at(hour=12))
    forward, backward = detect_contradictions([a, b]), detect_contradictions([b, a])
    assert forward[0].details["velocidad_requerida_kmh"] is None
    assert "al mismo tiempo" in forward[0].summary and forward[0].severity == "alta"
    assert sorted(forward[0].claim_ids) == sorted(backward[0].claim_ids)


def test_nearby_places_are_the_same_place():
    claims = [seen("a", OBELISCO, at(hour=12)), seen("b", CONGRESO, at(hour=12))]
    assert detect_contradictions(claims) == []
    strict = detect_contradictions(claims, ContradictionConfig(min_distance_km=0.5))
    assert kinds(strict) == ["espacio_temporal"] and strict[0].severity == "media"


def test_time_slack_absorbs_imprecise_timestamps():
    claims = [seen("a", OBELISCO, at(hour=12, minute=0)), seen("b", ROSARIO, at(hour=12, minute=15))]
    assert kinds(detect_contradictions(claims)) == ["espacio_temporal"]
    assert detect_contradictions(claims, ContradictionConfig(time_slack_minutes=10)) == []


def test_different_people_never_conflict_but_name_variants_do():
    other = seen("b", MADRID, at(hour=12), subject="Marta Albornoz")
    assert detect_contradictions([seen("a", BUENOS_AIRES, at(hour=12)), other]) == []
    variant = seen("b", MADRID, at(hour=12), subject="  juan  PEREZ ")
    assert kinds(detect_contradictions([seen("a", BUENOS_AIRES, at(hour=12)), variant])) == [
        "espacio_temporal"]


def test_claims_without_coordinates_or_time_are_ignored():
    no_coords = seen("a", Place(name="Tandil"), at(hour=12))
    no_time = seen("b", MADRID, None)
    assert detect_contradictions([no_coords, no_time, seen("c", BUENOS_AIRES, at(hour=12))]) == []


def test_non_presence_predicates_do_not_place_the_subject():
    lives = Claim(id="a", subject="Juan Pérez", predicate="domicilio", value="Madrid",
                  place=MADRID, start=at(hour=12), source="F1")
    assert detect_contradictions([lives, seen("b", BUENOS_AIRES, at(hour=12))]) == []


def test_timezones_are_compared_correctly():
    art = timezone(timedelta(hours=-3))
    # 09:00 en Argentina es 12:00 UTC: mismo instante, dos continentes.
    claims = [seen("a", BUENOS_AIRES, datetime(2024, 3, 10, 9, 0, tzinfo=art)),
              seen("b", MADRID, at(hour=12))]
    assert detect_contradictions(claims)[0].details["tiempo_disponible_s"] == 0
    # Sin zona se asume UTC.
    naive = [seen("a", BUENOS_AIRES, datetime(2024, 3, 10, 12, 0)), seen("b", MADRID, at(hour=12))]
    assert detect_contradictions(naive)[0].details["tiempo_disponible_s"] == 0


def test_intervals_of_stay():
    stay = seen("a", BUENOS_AIRES, at(hour=8), at(hour=18))
    # Visto en Rosario en medio de la estadía en Buenos Aires: imposible.
    inside = detect_contradictions([stay, seen("b", ROSARIO, at(hour=12))])
    assert inside[0].details["tiempo_disponible_s"] == 0
    # Visto en Rosario 30 minutos después de terminar la estadía: 280 km en media hora.
    assert kinds(detect_contradictions([stay, seen("b", ROSARIO, at(hour=18, minute=30))],
                                       ContradictionConfig(max_speed_kmh=300))) == ["espacio_temporal"]
    # Cinco horas después: viable.
    assert detect_contradictions([stay, seen("b", ROSARIO, at(hour=23))]) == []
    # Intervalo dado al revés: se ordena.
    reversed_stay = seen("a", BUENOS_AIRES, at(hour=18), at(hour=8))
    assert detect_contradictions([reversed_stay, seen("b", ROSARIO, at(hour=12))])


def test_day_precision_windows_are_not_treated_as_full_day_presence():
    """"El 10 de marzo estuvo en X" y "el 10 de marzo estuvo en Y" no se contradicen si da el día."""
    day = dict(start=at(hour=0), end=at(hour=23, minute=59), time_mode="dentro_de")
    assert detect_contradictions([seen("a", BUENOS_AIRES, **day), seen("b", ROSARIO, **day)]) == []
    # Pero Buenos Aires y Madrid en el mismo día sí: ni con las 24 horas alcanza… a 300 km/h.
    slow = ContradictionConfig(max_speed_kmh=300)
    assert kinds(detect_contradictions([seen("a", BUENOS_AIRES, **day), seen("b", MADRID, **day)],
                                       slow)) == ["espacio_temporal"]
    # Ventana contra instante: lo mejor que puede pasar es que haya estado al principio del día.
    window = seen("a", BUENOS_AIRES, **day)
    assert available_travel_seconds(window, seen("b", MADRID, at(hour=20))) == 20 * 3600
    assert available_travel_seconds(window, seen("b", MADRID, at(hour=2))) == (21 * 60 + 59) * 60
    # Ventana contra estadía que la cubre entera: no hay margen.
    cover = seen("b", MADRID, at(day=9), at(day=11))
    assert available_travel_seconds(window, cover) == 0


def test_many_claims_report_each_impossible_pair():
    claims = [seen("a", BUENOS_AIRES, at(hour=12), source="F1"),
              seen("b", MADRID, at(hour=13), source="F2"),
              seen("c", CORDOBA, at(hour=12, minute=30), source="F3"),
              seen("d", BUENOS_AIRES, at(hour=12, minute=5), source="F4")]
    found = detect_contradictions(claims)
    pairs = {tuple(c.claim_ids) for c in found}
    assert pairs == {("a", "b"), ("a", "c"), ("b", "c"), ("b", "d"), ("c", "d")}
    assert [c.id for c in found] == [f"C{i}" for i in range(1, 6)]


# --- (b) Atributos de valor único ------------------------------------------------------------


def fact(claim_id, predicate, value, source, subject="Juan Pérez", **kwargs):
    return Claim(id=claim_id, subject=subject, predicate=predicate, value=value, source=source,
                 **kwargs)


def test_conflicting_birth_dates_between_sources():
    found = detect_contradictions([fact("a", "fecha_nacimiento", "17/05/1990", "padron"),
                                   fact("b", "fecha_nacimiento", "1990-05-17", "perfil"),
                                   fact("c", "Fecha de nacimiento", "17 de mayo de 1991", "nota")])
    assert kinds(found) == ["valor_unico"]
    c = found[0]
    assert c.severity == "alta" and c.sources == ["padron", "perfil", "nota"]
    assert c.claim_ids == ["a", "b", "c"]
    assert c.details["valores"] == {"1990-05-17": ["a", "b"], "1991-05-17": ["c"]}
    assert "error de tipeo" in c.summary and "sin que eso descarte" in c.summary


def test_same_value_in_different_formats_is_not_a_conflict():
    assert detect_contradictions([fact("a", "dni", "12.345.678", "F1"),
                                  fact("b", "DNI", "12345678", "F2"),
                                  fact("c", "dni", "DNI 12 345 678", "F3")]) == []
    assert normalize_value("dni", "12.345.678") == normalize_value("dni", "012345678") == "12345678"
    assert normalize_value("lugar_nacimiento", " Córdoba. ") == normalize_value(
        "lugar_nacimiento", "CORDOBA")


def test_conflicting_dni():
    found = detect_contradictions([fact("a", "dni", "12.345.678", "F1"),
                                   fact("b", "dni", "40.111.222", "F2")])
    c = found[0]
    assert c.kind == "valor_unico" and c.severity == "alta"
    assert c.details["distancia_edicion_minima"] >= 6 and "tipeo" not in c.summary
    typo = detect_contradictions([fact("a", "dni", "12.345.678", "F1"),
                                  fact("b", "dni", "12.345.679", "F2")])[0]
    assert typo.details["distancia_edicion_minima"] == 1 and "1 carácter," in typo.summary


def test_single_valued_severity_and_scope():
    same_source = detect_contradictions([fact("a", "dni", "12.345.678", "F1"),
                                         fact("b", "dni", "23.456.789", "F1")])
    assert same_source[0].severity == "baja" and same_source[0].details["entre_fuentes"] is False
    place = detect_contradictions([fact("a", "lugar_nacimiento", "Rosario", "F1"),
                                   fact("b", "lugar_nacimiento", "Tandil", "F2")])
    assert place[0].severity == "media"
    # Predicados multivaluados no se comparan; valores vacíos tampoco.
    assert detect_contradictions([fact("a", "telefono", "111", "F1"),
                                  fact("b", "telefono", "222", "F2"),
                                  fact("c", "dni", "", "F1"), fact("d", "dni", "12.345.678", "F2")]) == []
    custom = ContradictionConfig(single_valued_predicates={"grupo_sanguineo"})
    assert kinds(detect_contradictions([fact("a", "grupo_sanguineo", "A+", "F1"),
                                        fact("b", "grupo_sanguineo", "0-", "F2")], custom)) == [
        "valor_unico"]


# --- (c) Intervalos que se excluyen ------------------------------------------------------------


def state(claim_id, predicate, start, end=None, source="F1", value="", **kwargs):
    return Claim(id=claim_id, subject="Juan Pérez", predicate=predicate, value=value, start=start,
                 end=end, source=source, **kwargs)


def test_exclusive_states_overlapping():
    found = detect_contradictions([
        state("a", "detenido", at(day=1), at(day=20), source="juzgado"),
        state("b", "en_libertad", at(day=10), at(day=15), source="red-social")])
    assert kinds(found) == ["intervalos_excluyentes"]
    c = found[0]
    assert c.severity == "alta" and c.sources == ["juzgado", "red-social"]
    assert c.details["solapamiento_s"] == 5 * 86400
    assert "«detenido» y «en_libertad»" in c.summary and "5,0 días" in c.summary


def test_adjacent_or_disjoint_intervals_do_not_conflict():
    assert detect_contradictions([state("a", "detenido", at(day=1), at(day=10)),
                                  state("b", "en_libertad", at(day=10), at(day=15))]) == []
    assert detect_contradictions([state("a", "detenido", at(day=1), at(day=5)),
                                  state("b", "en_libertad", at(day=6), at(day=9))]) == []


def test_instant_inside_exclusive_interval():
    found = detect_contradictions([state("a", "detenido", at(day=1), at(day=20)),
                                   state("b", "en_libertad", at(day=10))])
    assert kinds(found) == ["intervalos_excluyentes"] and found[0].severity == "media"
    assert "en el mismo instante" in found[0].summary


def test_open_ended_state():
    found = detect_contradictions([state("a", "fallecido", at(year=2020), open_end=True),
                                   state("b", "vivo", at(year=2023), at(year=2023, month=6))])
    assert kinds(found) == ["intervalos_excluyentes"] and found[0].severity == "alta"
    before = detect_contradictions([state("a", "fallecido", at(year=2020), open_end=True),
                                    state("b", "vivo", at(year=2019), at(year=2019, month=6))])
    assert before == []


def test_exclusive_values_of_same_predicate():
    found = detect_contradictions([
        state("a", "detenido_en", at(day=1), at(day=20), value="Unidad 9"),
        state("b", "detenido_en", at(day=5), at(day=8), value="Comisaría 3", source="F2"),
        state("c", "detenido_en", at(day=6), at(day=7), value="unidad 9", source="F3")])
    assert [tuple(c.claim_ids) for c in found] == [("a", "b"), ("b", "c")]
    # Trabajar en dos lugares a la vez no es excluyente.
    assert detect_contradictions([
        state("a", "trabaja_en", at(day=1), at(day=20), value="A"),
        state("b", "trabaja_en", at(day=5), at(day=8), value="B")]) == []


def test_short_overlap_is_low_severity():
    found = detect_contradictions([
        state("a", "detenido", at(hour=10), at(hour=12)),
        state("b", "en_libertad", at(hour=11, minute=40), at(hour=15))])
    assert found[0].severity == "baja"


# --- Propiedades generales ---------------------------------------------------------------------


def test_every_contradiction_lists_readings_without_choosing():
    claims = [seen("a", BUENOS_AIRES, at(hour=12), source="F1"), seen("b", MADRID, at(hour=13), source="F2"),
              fact("c", "dni", "12.345.678", "F1"), fact("d", "dni", "23.456.789", "F2"),
              state("e", "detenido", at(day=1), at(day=20)), state("f", "en_libertad", at(day=5), at(day=9))]
    found = detect_contradictions(claims)
    assert sorted(kinds(found)) == ["espacio_temporal", "intervalos_excluyentes", "valor_unico"]
    for c in found:
        reading_kinds = [r.kind for r in c.readings]
        assert {"suplantacion", "error_de_carga", "homonimos"} <= set(reading_kinds)
        assert all(r.description == READINGS[r.kind] for r in c.readings)
        assert c.sources and c.severity in ("baja", "media", "alta") and c.summary
        assert c.explanation == ""  # sin LLM no hay redacción ampliada, y no hace falta
        dumped = c.model_dump()
        assert not {"lectura_elegida", "veredicto", "conclusion"} & set(dumped)


def test_detection_is_deterministic_and_does_not_mutate_input():
    claims = [seen("a", BUENOS_AIRES, at(hour=12)), seen("b", MADRID, at(hour=13)),
              fact("c", "dni", "1.234.567", "F1"), fact("d", "dni", "7.654.321", "F2")]
    snapshot = [c.model_dump() for c in claims]
    first = detect_contradictions(claims)
    second = detect_contradictions(list(reversed(claims)))
    assert [c.model_dump() for c in claims] == snapshot
    assert [(c.kind, c.severity, sorted(c.claim_ids)) for c in first] == [
        (c.kind, c.severity, sorted(c.claim_ids)) for c in second]
    assert detect_contradictions([]) == []


# --- LLM (1): texto -> afirmaciones --------------------------------------------------------------

NOTE = ("Según la nota, Juan Pérez estuvo en Rosario el 10/03/2024 a las 14:30. Nació el 17 de mayo "
        "de 1990 y su DNI es 12.345.678. El 11 de marzo de 2024 fue visto en Córdoba.")
GAZETTEER = {"Rosario": (-32.9442, -60.6505), "Córdoba": (-31.4201, -64.1888)}


async def test_extract_claims_validates_quotes_and_builds_claims(make_client):
    reply = {"afirmaciones": [
        {"sujeto": "Juan Pérez", "predicado": "ubicado_en", "valor": "Rosario",
         "desde": "2024-03-10T14:30", "hasta": None, "lugar": "Rosario",
         "cita": "Juan Pérez estuvo en Rosario el 10/03/2024 a las 14:30", "confianza": 0.9},
        {"sujeto": "Juan Pérez", "predicado": "Fecha de nacimiento", "valor": "17/05/1990",
         "cita": "Nació el 17 de mayo de 1990", "confianza": 0.8},
        {"sujeto": "Juan Pérez", "predicado": "ubicado_en", "valor": "cordoba",
         "desde": "2024-03-11", "cita": "El 11 de marzo de 2024 fue visto en Córdoba"},
        # Inventadas: cita que no está, sujeto que no está.
        {"sujeto": "Juan Pérez", "predicado": "ubicado_en", "valor": "Madrid",
         "desde": "2024-03-10T15:00", "cita": "Juan Pérez fue visto en Madrid"},
        {"sujeto": "Carlos Gómez", "predicado": "dni", "valor": "99.999.999",
         "cita": "su DNI es 12.345.678"},
        {"sujeto": "Juan Pérez", "predicado": "dni", "valor": "12.345.678"},  # sin cita
    ]}
    client, server = make_client([reply])
    result = await extract_claims(NOTE, "nota-1", client, gazetteer=GAZETTEER)
    assert [c.id for c in result.claims] == ["nota-1:1", "nota-1:2", "nota-1:3"]
    rosario, birth, cordoba = result.claims
    art = timezone(timedelta(hours=-3))
    assert rosario.start == datetime(2024, 3, 10, 14, 30, tzinfo=art) and rosario.end is None
    assert (rosario.place.lat, rosario.place.lon) == GAZETTEER["Rosario"]
    assert rosario.source == "nota-1" and NOTE[rosario.offset:].startswith(rosario.quote)
    assert rosario.confidence == 0.9 and rosario.props["metodo"] == "llm"
    assert birth.predicate == "fecha_nacimiento" and birth.start is None
    # Fecha sin hora: ventana de un día, no presencia durante todo el día.
    assert cordoba.time_mode == "dentro_de"
    assert cordoba.end - cordoba.start == timedelta(days=1) - timedelta(seconds=1)
    assert cordoba.place.name == "cordoba" and cordoba.place.has_coords  # gazetteer sin tildes
    reasons = [d["reason"] for d in result.discarded]
    assert reasons == ["la cita no aparece en el texto fuente",
                       "el sujeto no aparece en el texto fuente"]  # la sin cita ni valida el esquema
    assert "<<<DOCUMENTO" in server.user() and "NO es confiable" in server.system()


async def test_llm_never_supplies_coordinates(make_client):
    reply = {"afirmaciones": [{"sujeto": "Juan Pérez", "predicado": "ubicado_en", "valor": "Rosario",
                               "lugar": "Rosario", "lat": 40.4, "lon": -3.7, "desde": "2024-03-10T14:30",
                               "cita": "Juan Pérez estuvo en Rosario"}]}
    client, _ = make_client([reply])
    result = await extract_claims(NOTE, "n", client)  # sin gazetteer
    assert result.claims[0].place.name == "Rosario" and not result.claims[0].place.has_coords


async def test_extract_claims_survives_llm_failure(make_client):
    client, _ = make_client(default=httpx.Response(500, text="x"), max_retries=0)
    result = await extract_claims(NOTE, "n", client)
    assert result.claims == [] and len(result.warnings) == 1


async def test_text_to_contradiction_end_to_end(make_client):
    """Dos fuentes, LLM simulado para estructurar, detección determinística."""
    other = "Registro del hotel: Juan Pérez se alojó en Madrid el 10/03/2024 a las 15:00."
    client, _ = make_client([
        {"afirmaciones": [{"sujeto": "Juan Pérez", "predicado": "ubicado_en", "valor": "Rosario",
                           "desde": "2024-03-10T14:30", "lugar": "Rosario",
                           "cita": "Juan Pérez estuvo en Rosario el 10/03/2024 a las 14:30"}]},
        {"afirmaciones": [{"sujeto": "Juan Pérez", "predicado": "ubicado_en", "valor": "Madrid",
                           "desde": "10/03/2024 15:00", "lugar": "Madrid",
                           "cita": "Juan Pérez se alojó en Madrid el 10/03/2024 a las 15:00"}]},
    ])
    places = {**GAZETTEER, "Madrid": (40.4168, -3.7038)}
    claims = (await extract_claims(NOTE, "nota", client, gazetteer=places)).claims
    claims += (await extract_claims(other, "hotel", client, gazetteer=places)).claims
    found = detect_contradictions(claims)
    assert kinds(found) == ["espacio_temporal"]
    assert found[0].sources == ["nota", "hotel"] and found[0].details["tiempo_disponible_s"] == 1800


# --- LLM (2): redacción ----------------------------------------------------------------------------


def _one_contradiction():
    claims = [seen("a", BUENOS_AIRES, at(hour=12), source="red-social", quote="Acá en el Obelisco"),
              seen("b", MADRID, at(hour=13), source="hotel")]
    return claims, detect_contradictions(claims)


async def test_explanation_from_llm_is_attached_without_changing_detection(make_client):
    claims, found = _one_contradiction()
    text = ("Las dos fuentes ubican al sujeto en ciudades separadas por unos 10.000 km con una hora "
            "de diferencia. No se puede descartar un error de carga ni el uso de la cuenta por "
            "terceros; también es posible que se trate de homónimos.")
    client, server = make_client([f"<think>veamos</think>{text}"])
    explained = await explain_contradictions(found, claims, client)
    assert explained[0].explanation == text and explained[0].explanation_source == "llm"
    assert explained[0].model_dump(exclude={"explanation", "explanation_source"}) == \
        found[0].model_dump(exclude={"explanation", "explanation_source"})
    assert found[0].explanation == ""  # no muta la entrada
    assert "Acá en el Obelisco" in server.user() and "<<<DATOS" in server.user()
    assert "response_format" not in server.chat_requests[0]


@pytest.mark.parametrize("bad", [
    "Sin dudas se trata de una suplantación de identidad.",
    "Está comprobado que Juan Pérez es el autor del hecho.",
    "",
    "x" * 2000,
    httpx.Response(500, text="boom"),
])
async def test_bad_explanations_fall_back_to_template(make_client, bad):
    claims, found = _one_contradiction()
    client, _ = make_client(default=bad, max_retries=0)
    explained = await explain_contradictions(found, claims, client)
    assert explained[0].explanation == found[0].summary
    assert explained[0].explanation_source == "plantilla"


async def test_explanation_without_client_uses_template():
    claims, found = _one_contradiction()
    explained = await explain_contradictions(found, claims, None)
    assert explained[0].explanation == found[0].summary
