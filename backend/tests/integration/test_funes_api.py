"""FUNES desde la API con un LLM simulado (MockTransport): NER, contradicciones, informe y sus fallos.

Qué se verifica: lo del LLM llega como propuesta, el NER entrega las reglas aunque el LLM falle, y el
informe y las contradicciones con fuentes responden 503 con un mensaje claro cuando el LLM no está.
"""

import json
from pathlib import Path

import httpx
import pytest

from aleph.api.services.outbound import llm_http
from aleph.core.config import get_settings
from aleph.main import app

NER_TEXT = ("Juan Pérez estuvo en Rosario el 3 de marzo de 2026. Contacto: juan.perez@ejemplo.com.ar, "
            "DNI 12.345.678.")
CLAIMS_TEXT = ("Registro de acceso: Ana Gómez ingresó en Rosario el 12/03/2026 a las 14:00. "
               "Publicación: Ana Gómez aparece en Ushuaia el 12/03/2026 a las 14:20.")
GAZETTEER = {"Rosario": [-32.9442, -60.6505], "Ushuaia": [-54.8019, -68.3030]}
NER_JSON = {
    "entidades": [
        {"id": "e1", "tipo": "persona", "texto": "Juan Pérez", "nombre": "Juan Pérez", "confianza": 0.9},
        {"id": "e2", "tipo": "lugar", "texto": "Rosario", "confianza": 0.9},
    ],
    "relaciones": [
        {"origen": "e1", "destino": "e2", "tipo": "ubicado_en", "cita": "Juan Pérez estuvo en Rosario",
         "confianza": 0.8},
    ],
}
CLAIMS_JSON = {"afirmaciones": [
    {"sujeto": "Ana Gómez", "predicado": "visto_en", "valor": "Rosario", "desde": "12/03/2026 14:00",
     "lugar": "Rosario", "cita": "Ana Gómez ingresó en Rosario el 12/03/2026 a las 14:00", "confianza": 0.8},
    {"sujeto": "Ana Gómez", "predicado": "visto_en", "valor": "Ushuaia", "desde": "12/03/2026 14:20",
     "lugar": "Ushuaia", "cita": "Ana Gómez aparece en Ushuaia el 12/03/2026 a las 14:20", "confianza": 0.8},
]}
REPORT_JSON = {
    "resumen_ejecutivo": [{"texto": "La campaña reúne entidades e hipótesis pendientes de revisión.",
                           "ids": ["E1", "H1"]}],
    "hallazgos": [{"texto": "Hay una contradicción que requiere revisión.", "ids": ["C9"]}],
    "hipotesis": [{"texto": "Las cuentas podrían pertenecer al mismo operador.", "ids": ["H1"],
                   "confianza": "media"}],
    "vacios": [{"texto": "Falta confirmar la atribución.", "ids": []}],
}
CONTRA_CLAIMS = [
    {"id": "c1", "subject": "Ana Gómez", "predicate": "visto_en", "value": "Rosario",
     "start": "2026-03-12T14:00:00-03:00", "place": {"name": "Rosario", "lat": -32.9442, "lon": -60.6505},
     "source": "Registro A", "quote": "Ana Gómez en Rosario"},
    {"id": "c2", "subject": "Ana Gómez", "predicate": "visto_en", "value": "Ushuaia",
     "start": "2026-03-12T14:20:00-03:00", "place": {"name": "Ushuaia", "lat": -54.8019, "lon": -68.3030},
     "source": "Publicación B", "quote": "Ana Gómez en Ushuaia"},
]


def _chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"id": "x", "object": "chat.completion", "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]})


def llm_handler(request: httpx.Request) -> httpx.Response:
    """Servidor simulado compatible con OpenAI: responde según el esquema que pide el motor."""
    body = json.loads(request.content)
    name = ((body.get("response_format") or {}).get("json_schema") or {}).get("name", "")
    payloads = {"llmextraction": NER_JSON, "llmclaims": CLAIMS_JSON, "llmreport": REPORT_JSON}
    if name in payloads:
        return _chat(json.dumps(payloads[name], ensure_ascii=False))
    return _chat("Lectura estimativa: los datos sugieren dos presencias difíciles de conciliar, a confirmar.")


@pytest.fixture
def llm_up(mock_http):
    app.dependency_overrides[llm_http] = lambda: mock_http(llm_handler)


def _upload_text(client, headers, case_id: int, text: str, name: str = "nota.txt") -> int:
    response = client.post(f"/api/cases/{case_id}/sources/upload", headers=headers,
                           files={"file": (name, text.encode("utf-8"), "text/plain")},
                           data={"reliability": "B", "credibility": "2"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ---------------------------------------------------------------- NER


def test_ner_con_llm_propone_y_no_confirma(client, token, fresh_case, llm_up):
    headers = token("analista-demo")
    out = client.post(f"/api/cases/{fresh_case}/funes/ner", headers=headers, json={"text": NER_TEXT})
    assert out.status_code == 201, out.text
    body = out.json()
    assert body["llm_status"] == "usado" and body["warnings"] == []
    labels = {(e["type"], e["label"]) for e in body["entities"]}
    assert {("person", "Juan Pérez"), ("location", "Rosario"), ("email", "juan.perez@ejemplo.com.ar"),
            ("document", "DNI 12.345.678")} <= labels
    assert all(e["status"] == "proposed" for e in body["entities"])
    assert any(r["type"] == "located_in" and r["status"] == "proposed" for r in body["relations"])


def test_ner_con_llm_caido_entrega_las_reglas_con_aviso(client, token, fresh_case):
    headers = token("analista-demo")  # el transporte por defecto no llega a ningún host
    out = client.post(f"/api/cases/{fresh_case}/funes/ner", headers=headers, json={"text": NER_TEXT})
    assert out.status_code == 201, out.text
    body = out.json()
    assert body["llm_status"] == "no_responde"
    assert any("no respondió" in w for w in body["warnings"])
    labels = {(e["type"], e["label"]) for e in body["entities"]}
    assert ("email", "juan.perez@ejemplo.com.ar") in labels and ("document", "DNI 12.345.678") in labels
    assert ("person", "Juan Pérez") not in labels  # lo que solo el LLM podía ver, no aparece


def test_ner_solo_reglas_y_sin_configuracion(client, token, fresh_case, settings):
    headers = token("analista-demo")
    rules = client.post(f"/api/cases/{fresh_case}/funes/ner", headers=headers,
                        json={"text": NER_TEXT, "use_llm": False}).json()
    assert rules["llm_status"] == "omitido" and rules["entities"]
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"llm_base_url": ""})
    unconfigured = client.post(f"/api/cases/{fresh_case}/funes/ner", headers=headers, json={"text": NER_TEXT})
    assert unconfigured.status_code == 201
    assert unconfigured.json()["llm_status"] == "no_configurado"
    assert any("ALEPH_LLM_BASE_URL" in w for w in unconfigured.json()["warnings"])


def test_ner_desde_una_fuente_y_alteracion_detectada(client, token, fresh_case, settings):
    headers = token("analista-demo")
    source_id = _upload_text(client, headers, fresh_case, NER_TEXT)
    out = client.post(f"/api/cases/{fresh_case}/funes/ner", headers=headers,
                      json={"source_id": source_id, "use_llm": False})
    assert out.status_code == 201, out.text
    assert out.json()["source_id"] != source_id  # la corrida tiene su propia fuente, con el texto analizado
    stored = next(Path(settings.data_dir, "cases", str(fresh_case), "sources").glob("*.txt"))
    stored.write_text("Texto distinto del original", encoding="utf-8")
    altered = client.post(f"/api/cases/{fresh_case}/funes/ner", headers=headers,
                          json={"source_id": source_id, "use_llm": False})
    assert altered.status_code == 409 and "alterada" in altered.json()["detail"]
    both = client.post(f"/api/cases/{fresh_case}/funes/ner", headers=headers,
                       json={"text": "x", "source_id": source_id})
    assert both.status_code == 422


# ---------------------------------------------------------------- contradicciones


def test_contradicciones_con_afirmaciones_no_necesitan_llm(client, token, demo_case):
    out = client.post(f"/api/cases/{demo_case}/funes/contradictions", headers=token("analista-demo"),
                      json={"claims": CONTRA_CLAIMS})
    assert out.status_code == 200, out.text
    body = out.json()
    assert len(body["contradictions"]) == 1
    found = body["contradictions"][0]
    assert found["kind"] == "espacio_temporal" and found["severity"] == "alta"
    assert found["explanation_source"] == "plantilla" and found["explanation"]  # sin LLM: redacción fija
    assert body["llm_status"] == "omitido"


def test_contradicciones_con_fuentes_y_llm(client, token, fresh_case, llm_up):
    headers = token("analista-demo")
    source_id = _upload_text(client, headers, fresh_case, CLAIMS_TEXT, "registro.txt")
    out = client.post(f"/api/cases/{fresh_case}/funes/contradictions", headers=headers,
                      json={"source_ids": [source_id], "gazetteer": GAZETTEER})
    assert out.status_code == 200, out.text
    body = out.json()
    assert {c["value"] for c in body["claims"]} == {"Rosario", "Ushuaia"}
    assert {c["source"] for c in body["claims"]} == {"registro.txt"}  # la fuente de la que salieron
    assert len(body["contradictions"]) == 1 and body["contradictions"][0]["explanation"]
    assert body["llm_status"] == "usado"


def test_contradicciones_con_fuentes_y_llm_caido_responde_503(client, token, fresh_case):
    headers = token("analista-demo")
    source_id = _upload_text(client, headers, fresh_case, CLAIMS_TEXT, "registro.txt")
    out = client.post(f"/api/cases/{fresh_case}/funes/contradictions", headers=headers,
                      json={"source_ids": [source_id]})
    assert out.status_code == 503
    assert "El LLM de FUNES no respondió" in out.json()["detail"]


# ---------------------------------------------------------------- informe


def test_informe_con_llm_valida_las_referencias(client, token, demo_case, llm_up):
    out = client.post(f"/api/cases/{demo_case}/funes/report", headers=token("analista-demo"), json={})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["generated_by"] == "llm"
    assert body["markdown"].startswith("# Borrador de informe de inteligencia")
    assert "## 3. Hipótesis y nivel de confianza" in body["markdown"]
    assert "[ID INEXISTENTE: C9]" in body["markdown"]  # el modelo citó un id que no existe en el caso
    assert "C9" in body["invalid_ids"]


def test_informe_sin_llm_responde_503(client, token, demo_case):
    out = client.post(f"/api/cases/{demo_case}/funes/report", headers=token("analista-demo"), json={})
    assert out.status_code == 503 and "no respondió" in out.json()["detail"]


def test_informe_sin_configuracion_responde_503(client, token, demo_case, settings):
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"llm_base_url": ""})
    out = client.post(f"/api/cases/{demo_case}/funes/report", headers=token("analista-demo"), json={})
    assert out.status_code == 503 and "no está configurado" in out.json()["detail"]
