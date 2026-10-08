"""Informe y resumen: estructura fija, ids validados, lenguaje estimativo."""

import re

import httpx
import pytest

from aleph.core.schemas import ClusterResult, EntityRecord, PairResult, RelationRecord
from aleph.funes.contradictions import Contradiction, Reading
from aleph.funes.language import find_categorical
from aleph.funes.report import CaseData, SourceRef, build_inventory, draft_report, summarize_case

HEADINGS = ["## 1. Resumen ejecutivo", "## 2. Hallazgos", "## 3. Hipótesis y nivel de confianza",
            "## 4. Vacíos de información", "## 5. Fuentes"]


def make_case() -> CaseData:
    return CaseData(
        title="Operación Ejemplo",
        purpose="Práctica con datos sintéticos",
        entities=[
            EntityRecord(type="person", label="Juan Pérez", confidence=0.9, ref="n1"),
            EntityRecord(type="organization", label="Cooperativa El Aleph", confidence=0.8, ref="n2"),
            EntityRecord(type="account", label="bluesky:tano_ok", confidence=1.0, ref="n3"),
        ],
        relations=[RelationRecord(src_ref="n1", dst_ref="n2", type="member_of", confidence=0.7),
                   RelationRecord(src_ref="n1", dst_ref="fantasma", type="uses")],
        pairs=[PairResult(a="bluesky:tano_ok", b="mastodon:el_tano", score=0.62, confidence="media",
                          summary="Hipótesis de mismo operador por estilo y horarios.")],
        clusters=[ClusterResult(members=["bluesky:tano_ok", "mastodon:el_tano"], cohesion=0.62)],
        contradictions=[Contradiction(
            id="C1", kind="espacio_temporal", severity="alta", subject="Juan Pérez",
            claim_ids=["a", "b"], sources=["nota", "hotel"],
            summary="Juan Pérez figura en Rosario y en Madrid con 30 minutos de diferencia.",
            readings=[Reading(kind="suplantacion", description="…"),
                      Reading(kind="error_de_carga", description="…"),
                      Reading(kind="homonimos", description="…")])],
        sources=[SourceRef(id="s1", title="Nota periodística", reference="https://ejemplo.org/n",
                           reliability="B2"),
                 SourceRef(id="s2", title="Registro de hotel")],
    )


GOOD = {
    "resumen_ejecutivo": [
        {"texto": "Es probable que Juan Pérez integre la Cooperativa El Aleph.", "ids": ["E1", "E2", "R1"]},
        {"texto": "Hay una contradicción espacio-temporal sin resolver [C1].", "ids": []},
    ],
    "hallazgos": [
        {"texto": "Los datos son compatibles con un vínculo entre la persona y la organización.",
         "ids": ["R1"]},
        {"texto": "Dos fuentes ubican al sujeto en ciudades distintas con media hora de diferencia; "
                  "no se puede descartar suplantación, error de carga ni homónimos.", "ids": ["C1", "F1", "F2"]},
    ],
    "hipotesis": [
        {"texto": "Es posible que las cuentas tano_ok y el_tano tengan un mismo operador.",
         "confianza": "media", "ids": ["H1", "G1"]},
    ],
    "vacios": [{"texto": "Falta confirmar el registro del hotel con una segunda fuente.", "ids": []}],
}


# --- Inventario ------------------------------------------------------------------------------


def test_inventory_assigns_canonical_ids():
    inventory, warnings = build_inventory(make_case())
    assert [i.id for i in inventory] == ["E1", "E2", "E3", "R1", "H1", "G1", "C1", "F1", "F2"]
    by_id = {i.id: i for i in inventory}
    assert by_id["E1"].original == "n1" and "persona: Juan Pérez" in by_id["E1"].text
    assert by_id["R1"].text.startswith("E1 --member_of--> E2")
    assert by_id["H1"].confidence == "media" and "mismo operador" in by_id["H1"].text
    assert "suplantacion, error_de_carga, homonimos" in by_id["C1"].text
    assert len(warnings) == 1 and "1 relaciones quedaron fuera" in warnings[0]


def test_inventory_truncates_large_cases_with_warning():
    case = CaseData(entities=[EntityRecord(type="person", label=f"P{i}", confidence=i / 400, ref=f"n{i}")
                              for i in range(300)])
    inventory, warnings = build_inventory(case, max_entities=50)
    assert len(inventory) == 50 and "50 entidades de mayor confianza de 300" in warnings[0]
    assert inventory[0].text.startswith("persona: P250")  # se conservan las de mayor confianza, en orden


# --- Informe -----------------------------------------------------------------------------------


async def test_report_structure_and_references(make_client):
    client, server = make_client([GOOD])
    report = await draft_report(make_case(), client)
    md = report.markdown
    assert report.generated_by == "llm" and report.invalid_ids == []
    positions = [md.index(h) for h in HEADINGS]
    assert positions == sorted(positions)
    assert md.startswith("# Borrador de informe de inteligencia — Operación Ejemplo")
    assert "hipótesis con evidencia para revisión de un analista" in md
    assert "- Es probable que Juan Pérez integre la Cooperativa El Aleph. [E1][E2][R1]" in md
    # Un id escrito adentro del texto se normaliza al final de la afirmación.
    assert "- Hay una contradicción espacio-temporal sin resolver. [C1]" in md
    assert "- **Confianza media.** Es posible que las cuentas" in md
    assert "- [F1] Nota periodística — https://ejemplo.org/n (valoración: B2)" in md
    assert "## Anexo. Datos citados" in md and "- [R1] E1 --member_of--> E2" in md
    assert "SIN RESPALDO" not in md and "ID INEXISTENTE" not in md and "REVISAR" not in md
    # Toda afirmación de las tres primeras secciones tiene al menos un id válido.
    for section in ("resumen_ejecutivo", "hallazgos", "hipotesis"):
        assert all(s.ids and not s.unsupported for s in report.sections[section])
    assert report.sections["vacios"][0].ids == [] and not report.sections["vacios"][0].unsupported
    # El prompt le pasa el inventario como dato delimitado y con sus ids.
    assert "[E1] persona: Juan Pérez" in server.user() and "<<<DATOS" in server.user()
    assert "Operación Ejemplo" in server.user()


async def test_nonexistent_ids_are_marked(make_client):
    reply = {
        "resumen_ejecutivo": [{"texto": "Es probable que haya un tercer implicado.", "ids": ["E1", "E99"]}],
        "hallazgos": [{"texto": "Es posible un vínculo con otra organización [R7].", "ids": ["X3"]},
                      {"texto": "No se puede descartar un viaje previo.", "ids": []},
                      {"texto": "Es posible que use la cuenta.", "ids": "e3, h1"}],
        "hipotesis": [], "vacios": [],
    }
    client, _ = make_client([reply])
    report = await draft_report(make_case(), client)
    assert report.invalid_ids == ["E99", "X3", "R7"]
    md = report.markdown
    assert "- Es probable que haya un tercer implicado. [E1][ID INEXISTENTE: E99]" in md
    assert ("- Es posible un vínculo con otra organización. [ID INEXISTENTE: X3]"
            "[ID INEXISTENTE: R7][SIN RESPALDO EN LOS DATOS]") in md
    assert "- No se puede descartar un viaje previo. [SIN RESPALDO EN LOS DATOS]" in md
    assert "- Es posible que use la cuenta. [E3][H1]" in md  # ids como texto y en minúscula
    assert "## Observaciones de validación automática" in md
    assert "ids que no existen en los datos del caso: E99, X3, R7" in md
    assert "2 afirmación(es) no citan ningún dato válido" in md
    # Los ids inexistentes nunca aparecen como referencia válida.
    assert not re.search(r"(?<!: )\[(E99|X3|R7)\]", md)


async def test_loose_tokens_in_prose_are_not_ids(make_client):
    reply = {"resumen_ejecutivo": [], "hipotesis": [], "vacios": [],
             "hallazgos": [{"texto": "Es posible que viajara a la cumbre del G20 en un F1.", "ids": ["E1"]}]}
    client, _ = make_client([reply])
    report = await draft_report(make_case(), client)
    assert report.invalid_ids == [] and report.sections["hallazgos"][0].ids == ["E1"]
    assert "cumbre del G20 en un F1. [E1]" in report.markdown


async def test_categorical_language_is_flagged(make_client):
    reply = {
        "resumen_ejecutivo": [{"texto": "Sin dudas Juan Pérez es el responsable.", "ids": ["E1"]}],
        "hallazgos": [{"texto": "Las cuentas son la misma persona.", "ids": ["H1"]},
                      {"texto": "No se puede afirmar que sean la misma persona.", "ids": ["H1"]}],
        "hipotesis": [], "vacios": [],
    }
    client, _ = make_client([reply])
    report = await draft_report(make_case(), client)
    flagged = [bool(s.categorical) for s in report.sections["resumen_ejecutivo"] + report.sections["hallazgos"]]
    assert flagged == [True, True, False]
    assert report.markdown.count("[REVISAR: lenguaje categórico]") == 2
    assert "2 afirmación(es) usan lenguaje categórico" in report.markdown


@pytest.mark.parametrize(("text", "categorical"), [
    ("Es probable que opere ambas cuentas.", False),
    ("No se puede descartar un mismo operador.", False),
    ("No está comprobado que haya viajado.", False),
    ("Está comprobado que viajó.", True),
    ("Juan Pérez cometió el hecho.", True),
    ("Indudablemente se trata del mismo operador.", True),
    ("Con total certeza es el autor.", True),
    ("Se trata de la misma persona.", True),
    ("No hay dudas de que fue él.", True),
])
def test_find_categorical(text, categorical):
    assert bool(find_categorical(text)) is categorical


async def test_hypothesis_confidence_cannot_exceed_cited_data(make_client):
    reply = {"resumen_ejecutivo": [], "hallazgos": [], "vacios": [], "hipotesis": [
        {"texto": "Es probable un mismo operador.", "confianza": "ALTA", "ids": ["H1"]},
        {"texto": "Es posible un vínculo laboral.", "confianza": "alto", "ids": ["R1"]},
        {"texto": "Es posible otra cuenta más.", "confianza": "alta", "ids": ["H9"]},
        {"texto": "Es posible algo.", "confianza": "qué sé yo", "ids": ["E1"]},
    ]}
    client, _ = make_client([reply])
    report = await draft_report(make_case(), client)
    levels = [s.confidence for s in report.sections["hipotesis"]]
    assert levels == ["media", "alta", "baja", "baja"]
    assert "confianza reducida de alta a media" in report.markdown
    assert report.sections["hipotesis"][2].unsupported


async def test_report_tolerates_bad_model_output(make_client):
    messy = ('<think>armo el informe</think>Acá va:\n```json\n{"resumen_ejecutivo": '
             '[{"texto": "Es probable un vínculo.", "ids": ["E1"],}, "ruido", {"sin_texto": 1}], '
             '"hallazgos": null, "hipotesis": [{"texto": "Es posible.", "ids": ["H1"]}]}\n```')
    client, _ = make_client([messy])
    report = await draft_report(make_case(), client)
    assert report.generated_by == "llm"
    assert [s.text for s in report.sections["resumen_ejecutivo"]] == ["Es probable un vínculo."]
    assert report.sections["hallazgos"] == [] and "_Sin hallazgos._" in report.markdown
    assert report.sections["hipotesis"][0].confidence == "baja"  # sin nivel declarado: el más bajo
    assert all(h in report.markdown for h in HEADINGS)


@pytest.mark.parametrize("bad", [
    "no puedo ayudar con eso", httpx.Response(500, text="x"), {"resumen_ejecutivo": []},
])
async def test_report_falls_back_to_template(make_client, bad):
    client, _ = make_client(default=bad, max_retries=0)
    report = await draft_report(make_case(), client)
    assert report.generated_by == "plantilla" and report.warnings
    assert all(h in report.markdown for h in HEADINGS)
    assert "plantilla determinística" in report.markdown
    assert report.invalid_ids == [] and "ID INEXISTENTE" not in report.markdown
    valid = {i.id for i in report.inventory}
    assert all(set(s.ids) <= valid for group in report.sections.values() for s in group)
    assert report.sections["hipotesis"][0].confidence == "media"
    assert not any(s.categorical for group in report.sections.values() for s in group)


async def test_report_without_client_or_data():
    report = await draft_report(make_case(), None)
    assert report.generated_by == "plantilla" and "[C1]" in report.markdown
    empty = await draft_report(CaseData(), None)
    assert all(h in empty.markdown for h in HEADINGS)
    assert "_El caso no tiene fuentes cargadas._" in empty.markdown


# --- Resumen -----------------------------------------------------------------------------------


async def test_summary(make_client):
    client, server = make_client([{"resumen": [
        {"texto": "Es probable que Juan Pérez integre la cooperativa.", "ids": ["E1", "R1"]},
        {"texto": "No se puede descartar un mismo operador de dos cuentas.", "ids": ["H1", "H5"]},
        {"texto": "Es culpable.", "ids": []},
    ]}])
    summary = await summarize_case(make_case(), client)
    assert summary.generated_by == "llm" and summary.invalid_ids == ["H5"]
    assert summary.markdown.startswith("**Resumen del caso — Operación Ejemplo**")
    assert "[E1][R1]" in summary.markdown and "[H1][ID INEXISTENTE: H5]" in summary.markdown
    assert "- Es culpable. [SIN RESPALDO EN LOS DATOS][REVISAR: lenguaje categórico]" in summary.markdown
    assert "entre 3 y\n6 afirmaciones" in server.system()


async def test_summary_bare_list_and_fallback(make_client):
    client, _ = make_client([[{"texto": "Es posible un vínculo.", "ids": ["R1"]}]])
    assert (await summarize_case(make_case(), client)).statements[0].ids == ["R1"]
    client, _ = make_client(default="perdón", max_retries=0)
    fallback = await summarize_case(make_case(), client)
    assert fallback.generated_by == "plantilla" and fallback.statements[0].ids
