"""NER con LLM simulado: validación de citas, deduplicación y tolerancia a salidas malas."""

import httpx

from aleph.core.schemas import ENTITY_TYPES
from aleph.funes.citations import locate_quote
from aleph.funes.ner import extract_entities, normalize_person_name, person_signature

TEXT = (
    "Según el parte, Juan Pérez, DNI 12.345.678, fue visto en Rosario junto a Marta Albornoz. "
    "PEREZ, Juan integra la Cooperativa El Aleph, que preside Albornoz. En el barrio lo llaman "
    "«el Tano». J. Pérez habría viajado luego a Córdoba."
)

GOOD = {
    "entidades": [
        {"id": "e1", "tipo": "persona", "texto": "Juan Pérez", "nombre": "Juan Pérez", "confianza": 0.9},
        {"id": "e2", "tipo": "persona", "texto": "PEREZ, Juan", "nombre": "Juan Pérez", "confianza": 0.8},
        {"id": "e3", "tipo": "persona", "texto": "J. Pérez", "confianza": 0.6},
        {"id": "e4", "tipo": "persona", "texto": "Marta Albornoz", "confianza": 0.9},
        {"id": "e5", "tipo": "organizacion", "texto": "Cooperativa El Aleph", "confianza": 0.85},
        {"id": "e6", "tipo": "lugar", "texto": "Rosario", "confianza": 0.9},
        {"id": "e7", "tipo": "alias", "texto": "el Tano", "confianza": 0.7},
    ],
    "relaciones": [
        {"origen": "e2", "destino": "e5", "tipo": "miembro_de",
         "cita": "PEREZ, Juan integra la Cooperativa El Aleph", "confianza": 0.8},
        {"origen": "e1", "destino": "r1", "tipo": "identificado_por",
         "cita": "Juan Pérez, DNI 12.345.678", "confianza": 0.95},
        {"origen": "e7", "destino": "e1", "tipo": "alias_de",
         "cita": "En el barrio lo llaman «el Tano»", "confianza": 0.7},
    ],
}


def by_label(result, label):
    return next(e for e in result.entities if e.label == label)


# --- Citas -------------------------------------------------------------------------------


def test_locate_quote_exact_and_tolerant():
    source = "El  señor  PÉREZ,\nJuan dijo “no fui yo” ayer."
    exact = locate_quote(source, "PÉREZ")
    assert exact and exact.exact and source[exact.start:exact.end] == "PÉREZ"
    folded = locate_quote(source, "perez, juan dijo \"no fui yo\" ayer")
    assert folded and not folded.exact
    assert source[folded.start:folded.end] == "PÉREZ,\nJuan dijo “no fui yo” ayer"
    trimmed = locate_quote(source, "«Juan dijo»…")
    assert trimmed and source[trimmed.start:trimmed.end] == "Juan dijo"
    assert locate_quote(source, "Carlos Gómez") is None
    assert locate_quote(source, "") is None and locate_quote(source, "..") is None
    assert locate_quote("", "algo") is None


# --- Nombres -----------------------------------------------------------------------------


def test_person_name_normalization():
    assert normalize_person_name("PEREZ, Juan") == "Juan Perez"
    assert normalize_person_name("Dr. juan  PÉREZ") == "Juan Pérez"
    assert normalize_person_name("J. Pérez") == "J. Pérez"
    assert normalize_person_name("MARÍA DE LOS ÁNGELES GÓMEZ") == "María de los Ángeles Gómez"
    assert person_signature("PEREZ, Juan") == person_signature("Juan Pérez") == (("juan", "perez"), ())
    assert person_signature("Sr. J. Pérez") == (("perez",), ("j",))


# --- Camino feliz ------------------------------------------------------------------------


async def test_entities_relations_and_dedup(make_client):
    client, server = make_client([GOOD])
    result = await extract_entities(TEXT, client)
    assert result.warnings == [] and result.discarded == []

    # "Juan Pérez", "PEREZ, Juan" y "J. Pérez" son una sola persona propuesta.
    people = [e for e in result.entities if e.type == "person" and e.props.get("kind") != "alias"]
    assert sorted(p.label for p in people) == ["Juan Pérez", "Marta Albornoz"]
    juan = by_label(result, "Juan Pérez")
    assert juan.props["variantes"] == ["Juan Pérez", "PEREZ, Juan", "J. Pérez"]
    assert len(juan.props["menciones"]) == 3 and juan.confidence == 0.9

    for entity in result.entities:
        props = entity.props
        assert entity.type in ENTITY_TYPES and props["estado"] == "propuesta"
        assert props["metodo"] in ("regla", "llm")
        assert TEXT[props["offset"]:props["offset"] + len(props["cita"])] == props["cita"]
        assert entity.confidence <= (0.9 if props["metodo"] == "llm" else 1.0)

    alias = by_label(result, "el Tano")
    assert alias.type == "person" and alias.props["kind"] == "alias"
    dni = next(e for e in result.entities if e.props.get("regla") == "dni")
    org = by_label(result, "Cooperativa El Aleph")

    relations = {(r.src_ref, r.type, r.dst_ref): r for r in result.relations}
    assert set(relations) == {(juan.ref, "member_of", org.ref),
                              (juan.ref, "identified_by", dni.ref),
                              (alias.ref, "alias_of", juan.ref)}
    member = relations[(juan.ref, "member_of", org.ref)]
    assert member.props["cita"] == "PEREZ, Juan integra la Cooperativa El Aleph"
    assert member.props["metodo"] == "llm" and member.props["extremos_en_cita"] is True
    assert TEXT[member.props["offset"]:].startswith(member.props["cita"])
    assert member.confidence == 0.8
    # La cita del alias no nombra a Juan: respaldo débil, confianza reducida.
    weak = relations[(alias.ref, "alias_of", juan.ref)]
    assert weak.props["extremos_en_cita"] is False and weak.confidence < 0.7 * 0.7

    # El modelo recibió las entidades de reglas con su id para poder relacionarlas.
    assert 'r1: DNI "DNI 12.345.678"' in server.user()


async def test_ambiguous_initial_is_not_merged(make_client):
    text = "Declararon Juan Pérez y José Pérez. Después habló J. Pérez."
    client, _ = make_client([{"entidades": [
        {"id": "a", "tipo": "persona", "texto": "Juan Pérez"},
        {"id": "b", "tipo": "persona", "texto": "José Pérez"},
        {"id": "c", "tipo": "persona", "texto": "J. Pérez"},
    ]}])
    result = await extract_entities(text, client)
    assert sorted(e.label for e in result.entities) == ["J. Pérez", "José Pérez", "Juan Pérez"]


async def test_lone_surname_is_not_merged(make_client):
    text = "Juan Pérez llegó tarde. Pérez no declaró."
    client, _ = make_client([{"entidades": [
        {"id": "a", "tipo": "persona", "texto": "Juan Pérez"},
        {"id": "b", "tipo": "persona", "texto": "Pérez no declaró", "nombre": "Pérez"},
    ]}])
    result = await extract_entities(text, client)
    assert sorted(e.label for e in result.entities) == ["Juan Pérez", "Pérez"]
    # Citó una oración, pero la mención se acota al nombre que sí está en la cita.
    lone = by_label(result, "Pérez")
    assert lone.props["cita"] == "Pérez" and lone.props["offset"] == text.index("Pérez no")


# --- Alucinaciones -----------------------------------------------------------------------


async def test_invented_entities_and_relations_are_discarded(make_client):
    reply = {
        "entidades": [
            {"id": "e1", "tipo": "persona", "texto": "Juan Pérez"},
            {"id": "e2", "tipo": "persona", "texto": "Carlos Gómez"},  # no está en el texto
            {"id": "e3", "tipo": "organizacion", "texto": "Banda de los Monos"},  # tampoco
            {"id": "e4", "tipo": "lugar", "texto": "Rosario"},
            {"id": "e5", "tipo": "criptomoneda", "texto": "Rosario"},  # tipo fuera de la lista
            # Cita real, nombre inventado: la etiqueta sale del texto, no del modelo.
            {"id": "e6", "tipo": "persona", "texto": "Marta Albornoz", "nombre": "Marta Capone"},
        ],
        "relaciones": [
            {"origen": "e1", "destino": "e2", "tipo": "familiar_de", "cita": "Juan Pérez"},
            {"origen": "e1", "destino": "e4", "tipo": "ubicado_en",
             "cita": "Juan Pérez vive en Rosario desde 2010"},  # cita inventada
            {"origen": "e1", "destino": "e4", "tipo": "ubicado_en", "cita": ""},  # sin cita
            {"origen": "e1", "destino": "e1", "tipo": "vinculado_con", "cita": "Juan Pérez"},
            {"origen": "e1", "destino": "e99", "tipo": "usa", "cita": "Juan Pérez"},
        ],
    }
    client, _ = make_client([reply])
    result = await extract_entities(TEXT, client)
    labels = {e.label for e in result.entities}
    assert {"Juan Pérez", "Rosario", "Marta Albornoz"} <= labels
    assert not labels & {"Carlos Gómez", "Banda de los Monos", "Marta Capone"}
    assert result.relations == []
    reasons = [(d.kind, d.reason) for d in result.discarded]
    assert reasons.count(("entidad", "la cita no aparece en el texto fuente")) == 2
    assert ("entidad", "tipo desconocido: 'criptomoneda'") in reasons
    assert reasons.count(("relacion", "la cita no aparece en el texto fuente")) == 2
    assert reasons.count(("relacion", "referencia a una entidad inexistente o descartada")) == 2
    assert ("relacion", "relación de una entidad consigo misma") in reasons


async def test_paragraph_as_mention_is_rejected(make_client):
    client, _ = make_client([{"entidades": [
        {"id": "e1", "tipo": "organizacion", "texto": TEXT[:200]}]}])
    result = await extract_entities(TEXT, client)
    assert [e for e in result.entities if e.type == "organization"] == []
    assert result.discarded[0].reason == "la cita es demasiado larga para ser una mención"


async def test_accent_and_case_differences_are_tolerated_with_penalty(make_client):
    client, _ = make_client([{"entidades": [
        {"id": "e1", "tipo": "lugar", "texto": "cordoba", "confianza": 0.8}]}])
    result = await extract_entities(TEXT, client)
    place = next(e for e in result.entities if e.type == "location")
    assert place.label == "Córdoba"  # lo que dice el texto, no lo que escribió el modelo
    assert place.props["cita_exacta"] is False and place.confidence == round(0.8 * 0.85, 3)


async def test_unknown_relation_type_falls_back(make_client):
    client, _ = make_client([{
        "entidades": [{"id": "a", "tipo": "persona", "texto": "Juan Pérez"},
                      {"id": "b", "tipo": "persona", "texto": "Marta Albornoz"}],
        "relaciones": [{"origen": "a", "destino": "b", "tipo": "Socio Comercial De",
                        "cita": "Juan Pérez, DNI 12.345.678, fue visto en Rosario junto a Marta Albornoz"}],
    }])
    result = await extract_entities(TEXT, client)
    assert result.relations[0].type == "related_to"
    assert result.relations[0].props["tipo_original"] == "Socio Comercial De"


async def test_llm_confidence_is_capped_and_coerced(make_client):
    client, _ = make_client([{"entidades": [
        {"id": 1, "tipo": "Persona", "texto": "Juan Pérez", "confianza": "100%"},
        {"id": 2, "tipo": "LUGAR", "texto": "Rosario", "confianza": "alta"},
        {"id": 3, "tipo": "lugar", "texto": "Córdoba", "confianza": None}]}])
    result = await extract_entities(TEXT, client)
    assert by_label(result, "Juan Pérez").confidence == 0.9
    assert by_label(result, "Rosario").confidence == 0.85
    assert by_label(result, "Córdoba").confidence == 0.6


async def test_llm_location_merges_with_rule_address(make_client):
    text = "El allanamiento fue en Av. Corrientes 1234."
    client, _ = make_client([{"entidades": [
        {"id": "e1", "tipo": "lugar", "texto": "Av. Corrientes 1234"}]}])
    result = await extract_entities(text, client)
    assert len(result.entities) == 1
    assert result.entities[0].props["metodo"] == "regla"
    assert result.entities[0].props["metodos"] == ["llm", "regla"]


# --- Salidas malas del modelo --------------------------------------------------------------


async def test_think_block_and_text_around_json(make_client):
    reply = ('<think>Veo a Carlos Gómez… no, no está. {"entidades": []}</think>\n'
             'Estas son las entidades:\n```json\n'
             '{"entidades": [{"id": "e1", "tipo": "persona", "texto": "Marta Albornoz",}],}\n'
             '```\nAvisame si necesitás algo más.')
    client, _ = make_client([reply])
    result = await extract_entities(TEXT, client)
    assert "Marta Albornoz" in {e.label for e in result.entities}


async def test_broken_json_then_good_retry(make_client):
    client, server = make_client(['{"entidades": [{"id": "e1", "tipo": "pers', GOOD])
    result = await extract_entities(TEXT, client)
    assert "Marta Albornoz" in {e.label for e in result.entities}
    assert len(server.chat_requests) == 2
    assert "Tu respuesta anterior no se pudo usar" in server.user(1)


async def test_truncated_json_keeps_complete_entities(make_client):
    reply = ('{"entidades": [{"id": "e1", "tipo": "persona", "texto": "Marta Albornoz"}, '
             '{"id": "e2", "tipo": "lugar", "texto": "Rosario"}, {"id": "e3", "tipo": "lug')
    client, server = make_client([reply])
    result = await extract_entities(TEXT, client)
    assert {"Marta Albornoz", "Rosario"} <= {e.label for e in result.entities}
    assert len(server.chat_requests) == 1


async def test_bare_list_and_malformed_items(make_client):
    reply = [{"tipo": "persona", "texto": "Marta Albornoz"}, {"tipo": "persona"}, "ruido",
             {"type": "lugar", "text": "Rosario"}]
    client, _ = make_client([reply])
    result = await extract_entities(TEXT, client)
    assert {"Marta Albornoz", "Rosario"} <= {e.label for e in result.entities}


async def test_llm_failure_keeps_rule_entities(make_client):
    for bad in ("no pienso responder en JSON", httpx.Response(500, text="boom"),
                httpx.ConnectError("caído")):
        client, _ = make_client(default=bad, max_retries=1)
        result = await extract_entities(TEXT, client)
        assert [e.props["regla"] for e in result.entities] == ["dni"]
        assert len(result.warnings) == 1 and "reglas" in result.warnings[0]


# --- Textos largos -------------------------------------------------------------------------


async def test_long_text_is_chunked_and_offsets_are_global(make_client):
    filler = "Nada relevante en esta oración de relleno. " * 40
    text = f"{filler}Aparece Juan Pérez en la esquina. {filler}Luego llega Marta Albornoz. {filler}"

    def reply(body):
        document = body["messages"][-1]["content"]
        found = []
        for i, name in enumerate(("Juan Pérez", "Marta Albornoz")):
            if name in document:
                found.append({"id": f"e{i}", "tipo": "persona", "texto": name})
        return {"entidades": found}

    client, server = make_client(default=reply)
    result = await extract_entities(text, client, max_chars=900, overlap=150)
    assert len(server.chat_requests) > 3
    assert sorted(e.label for e in result.entities) == ["Juan Pérez", "Marta Albornoz"]
    for entity in result.entities:
        assert text[entity.props["offset"]:].startswith(entity.label)
        # Aunque el solapamiento haga que dos fragmentos vean el nombre, es una sola mención.
        assert "menciones" not in entity.props


async def test_same_local_ids_in_different_chunks_do_not_cross(make_client):
    part_a = "Juan Pérez trabaja para la Cooperativa El Aleph. " + "Relleno sin datos. " * 30
    part_b = "Marta Albornoz dirige el Club Atlético Ficticio. " + "Relleno sin datos. " * 30
    text = part_a + part_b

    def reply(body):
        document = body["messages"][-1]["content"]
        if "Juan Pérez" in document:
            return {"entidades": [{"id": "e1", "tipo": "persona", "texto": "Juan Pérez"},
                                  {"id": "e2", "tipo": "organizacion", "texto": "Cooperativa El Aleph"}],
                    "relaciones": [{"origen": "e1", "destino": "e2", "tipo": "trabaja_para",
                                    "cita": "Juan Pérez trabaja para la Cooperativa El Aleph"}]}
        return {"entidades": [{"id": "e1", "tipo": "persona", "texto": "Marta Albornoz"},
                              {"id": "e2", "tipo": "organizacion", "texto": "Club Atlético Ficticio"}],
                "relaciones": [{"origen": "e1", "destino": "e2", "tipo": "trabaja_para",
                                "cita": "Marta Albornoz dirige el Club Atlético Ficticio"}]}

    client, _ = make_client(default=reply)
    result = await extract_entities(text, client, max_chars=len(part_a), overlap=0)
    labels = {e.ref: e.label for e in result.entities}
    pairs = {(labels[r.src_ref], labels[r.dst_ref]) for r in result.relations}
    assert pairs == {("Juan Pérez", "Cooperativa El Aleph"),
                     ("Marta Albornoz", "Club Atlético Ficticio")}
