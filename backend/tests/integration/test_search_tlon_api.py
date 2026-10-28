"""Búsqueda, preguntas al caso, plan OSINT externo y TLÖN sobre el caso de demostración, sin red.

El LLM y los embeddings se simulan con MockTransport: /embeddings devuelve vectores por palabras
clave y /chat/completions responde según el prompt (revisión de TLÖN o pregunta).
"""

import json

import httpx
import numpy as np
import pytest

from aleph.api.services import search as search_service
from aleph.api.services.outbound import llm_http
from aleph.main import app

ANALYST = "analista-demo"


def _vector(text: str) -> list[float]:
    """Embedding de juguete: dimensiones por tema, para que 'descuento' quede cerca de 'cupón'."""
    t = text.lower()
    topics = [("cupón", "cupon", "descuento", "promo"), ("billetera", "bitcoin", "btc", "pago"),
              ("dominio", "sitio", "web", "url"), ("correo", "email", "soporte")]
    v = np.array([1.0 if any(w in t for w in words) else 0.0 for words in topics] + [0.05])
    return (v / np.linalg.norm(v)).tolist()


def _handler(review: dict | None = None, answer: str = ""):
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": i, "embedding": _vector(t)}
                                                      for i, t in enumerate(body["input"])]})
        system = body["messages"][0]["content"]
        if "TLÖN" in system:
            user = body["messages"][-1]["content"]
            refs = sorted(set(__import__("re").findall(r"\[(T\d+)\]", user)))
            rv = review or {"revisiones": [{"id": r, "veredicto": "plausible",
                                            "razon": "Patrón compatible.", "verificar": "Revisar el WHOIS."}
                                           for r in refs],
                            "lectura": [{"texto": "Es posible que la campaña comparta infraestructura.",
                                         "ids": refs[-1:]}]}
            content = json.dumps(rv)
        else:
            content = answer
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return handle


@pytest.fixture(autouse=True)
def clear_cache():
    search_service._cache.clear()


@pytest.fixture
def llm_up(mock_http):
    def install(**kwargs):
        app.dependency_overrides[llm_http] = lambda: mock_http(_handler(**kwargs))
    return install


def test_busqueda_sin_tildes_encuentra_entidades_casos_y_hallazgos(client, token, demo_case):
    r = client.get("/api/search", params={"q": "cupon", "case_id": demo_case}, headers=token(ANALYST))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["semantic_status"] == "omitido"
    kinds = {i["kind"] for i in data["items"]}
    assert {"entity", "case", "finding"} <= kinds  # «Cupón» con tilde, buscado sin tilde
    assert all(i["case_id"] == demo_case for i in data["items"])
    assert data["items"][0]["ref"][0] in "EAPSFHK"


def test_busqueda_global_respeta_el_acceso_por_caso(client, token, demo_case, demo):
    from aleph.api import security
    from aleph.core.models import User

    with demo.factory() as session:
        intruso = User(username="intruso-busqueda", password_hash=security.hash_password("x" * 12), role="analyst")
        session.add(intruso)
        session.commit()
        uid = intruso.id
    from aleph.core.config import get_settings
    settings = app.dependency_overrides[get_settings]()
    tok, _ = security.create_token(uid, "analyst", settings)
    headers = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/search", params={"q": "cupon"}, headers=headers).json()["items"] == []
    assert client.get("/api/search", params={"q": "cupon", "case_id": demo_case}, headers=headers).status_code == 404


def test_busqueda_semantica_reordena_y_avisa_si_no_responde(client, token, demo_case, llm_up):
    # Sin servidor: queda por texto, con aviso
    r = client.get("/api/search", params={"q": "cupon", "case_id": demo_case, "semantic": True},
                   headers=token(ANALYST)).json()
    assert r["semantic_status"] == "no_responde" and r["warnings"]
    llm_up()
    r = client.get("/api/search", params={"q": "promo de descuento", "case_id": demo_case, "semantic": True},
                   headers=token(ANALYST)).json()
    assert r["semantic_status"] == "usado"
    assert r["items"] and r["items"][0]["semantic"] is not None
    # "promo de descuento" no aparece literal, pero por significado trae lo del cupón
    assert any("cup" in (i["snippet"] + i["title"]).lower() for i in r["items"][:5])


def test_preguntar_al_caso_cita_y_marca_lo_inventado(client, token, demo_case, llm_up):
    hits = client.get("/api/search", params={"q": "cupon", "case_id": demo_case}, headers=token(ANALYST)).json()
    ref = hits["items"][0]["ref"]
    llm_up(answer=f"El cupón aparece en publicaciones del caso [{ref}]. Hay otro dato [Z99].")
    r = client.post(f"/api/cases/{demo_case}/ask", json={"question": "¿Qué dice el cupón?"}, headers=token(ANALYST))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["llm_status"] == "usado"
    assert [c["ref"] for c in data["citations"]] == [ref]
    assert data["invalid_citations"] == ["Z99"] and "[Z99?]" in data["answer"]
    audit = client.get("/api/audit", params={"action": "funes.ask"}, headers=token("auditor-demo"))
    if audit.status_code == 200:
        assert "cupón" not in json.dumps(audit.json())  # se guarda el hash, no la pregunta


def test_preguntar_sin_llm_responde_503(client, token, demo_case):
    r = client.post(f"/api/cases/{demo_case}/ask", json={"question": "¿Qué pasó?"}, headers=token(ANALYST))
    assert r.status_code == 503 and "no respondió" in r.json()["detail"]


@pytest.mark.parametrize("q,kind,first", [
    ("@operador_sur", "handle", "connector:username_search"),
    ("soporte@verificacion-cuenta.example", "email", "tool:email_intel"),
    ("verificacion-cuenta.example", "domain", "connector:domain_intel"),
    ("8.8.8.8", "ip", "tool:asn"),
    ("CVE-2024-3400", "cve", "tool:vulns"),
])
def test_plan_de_busqueda_externa(client, token, demo_case, q, kind, first):
    r = client.get(f"/api/cases/{demo_case}/lookup", params={"q": q}, headers=token(ANALYST))
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["kind"] == kind and plan["actions"][0]["id"] == first
    assert all(a["path"].startswith(f"/cases/{demo_case}/") for a in plan["actions"])
    if kind == "handle":
        x = next(a for a in plan["actions"] if a["name"] == "x_api")
        assert x["available"] is False and "X_BEARER_TOKEN" in x["unavailable_reason"]


def test_tlon_propone_revisa_y_guarda_propuestas(client, token, fresh_case, llm_up):
    headers = token(ANALYST)
    base = f"/api/cases/{fresh_case}"
    ids = {}
    for g in range(12):
        for key, (type_, label) in {"p": ("person", f"Persona {g} (ficticia)"), "a": ("account", f"@op_{g}"),
                                    "m": ("email", f"op{g}@red{g}.test"), "d": ("domain", f"red{g}.test")}.items():
            r = client.post(f"{base}/entities", json={"type": type_, "label": label}, headers=headers)
            assert r.status_code == 201, r.text
            ids[(g, key)] = r.json()["id"]
        rels = [("p", "a", "controls"), ("a", "m", "uses_email"), ("m", "d", "belongs_to")]
        if g != 4:
            rels.append(("a", "d", "shares_domain"))
        for s, d, t in rels:
            r = client.post(f"{base}/relations", json={"src_id": ids[(g, s)], "dst_id": ids[(g, d)], "type": t},
                            headers=headers)
            assert r.status_code == 201, r.text
    llm_up()
    r = client.post(f"{base}/tlon/run", json={"min_percentile": 0.0, "max_hypotheses": 5}, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "ok" and out["hypotheses"], out["message"]
    assert (out["llm_status"], out["embeddings_status"]) == ("usado", "usado"), out["warnings"]
    assert out["quality"]["holdout_edges"] > 0 and out["source_id"]
    top = out["hypotheses"][0]
    # Encuentra el vínculo que se ocultó en el grupo 4
    assert (top["src"], top["dst"], top["type"]) == (ids[(4, "a")], ids[(4, "d")], "shares_domain")
    assert top["review"]["veredicto"] == "plausible" and top["relation_id"]
    assert out["created_relations"] == len(out["hypotheses"])
    assert out["lectura"][0]["ids"] == [out["hypotheses"][-1]["ref"]]
    rel = client.get(f"{base}/relations/{top['relation_id']}", headers=headers).json()
    assert rel["status"] == "proposed" and rel["props"]["motor"] == "tlon" and "funes" in rel["props"]
    latest = client.get(f"{base}/tlon/latest", headers=headers)
    assert latest.status_code == 200 and latest.json()["created_relations"] == out["created_relations"]

    # Si FUNES la descarta, no entra al grafo (y queda en el resultado para ver qué se filtró)
    assert client.delete(f"{base}/relations/{top['relation_id']}", headers=headers).status_code == 204
    llm_up(review={"revisiones": [{"id": "T1", "veredicto": "descartable", "razon": "No cierra.",
                                   "verificar": "-"}], "lectura": []})
    again = client.post(f"{base}/tlon/run", json={"min_percentile": 0.0, "max_hypotheses": 1},
                        headers=headers).json()
    assert (again["hypotheses"][0]["src"], again["hypotheses"][0]["dst"]) == (top["src"], top["dst"])
    assert again["hypotheses"][0]["review"]["veredicto"] == "descartable"
    assert again["hypotheses"][0]["relation_id"] is None and again["created_relations"] == 0

    # Lo que el analista rechaza no se vuelve a proponer
    llm_up()
    third = client.post(f"{base}/tlon/run", json={"min_percentile": 0.0, "max_hypotheses": 1},
                        headers=headers).json()
    rid = third["hypotheses"][0]["relation_id"]
    assert client.post(f"{base}/relations/{rid}/review", json={"decision": "reject"},
                       headers=headers).status_code == 200
    fourth = client.post(f"{base}/tlon/run", json={"min_percentile": 0.0, "max_hypotheses": 5},
                         headers=headers).json()
    assert all((h["src"], h["dst"]) != (top["src"], top["dst"]) for h in fourth["hypotheses"])


def test_tlon_sin_llm_igual_propone_con_aviso(client, token, demo_case):
    r = client.post(f"/api/cases/{demo_case}/tlon/run", json={"min_percentile": 0.0}, headers=token(ANALYST))
    assert r.status_code == 200, r.text
    out = r.json()
    if out["status"] == "ok" and out["hypotheses"]:
        assert out["llm_status"] == "no_responde"
        assert any("FUNES no pudo revisar" in w for w in out["warnings"])
        assert all(h["relation_id"] for h in out["hypotheses"])


def test_tlon_auditor_no_puede_correr(client, token, demo_case):
    r = client.post(f"/api/cases/{demo_case}/tlon/run", json={}, headers=token("auditor-demo"))
    assert r.status_code == 403
