"""Punta a punta sobre el caso de demostración: login, grafo, MENARD, revisión, IOCs, STIX y auditoría.

Corre sobre el caso que siembra `aleph.demo.seed` (SQLite en memoria, sin red).
"""

import json

import stix2

from aleph.demo import story

from .conftest import DEMO_PASSWORD

INFRA_TEXT = (
    "Alerta ficticia: el sitio https://nueva-campana.example/pago?ref=7 entrega el instalador con SHA-256 "
    "3f79bb7b435b05321651daefd374cdc681dc06faa65e374e38337b88ca046dea desde 192.0.2.77. Los pagos van a "
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4. Se parece a la técnica T1566.002."
)
PENDING_TEXT = "Hipótesis de mismo operador (MENARD) pendiente"
CONFIRMED_TEXT = "Hipótesis de mismo operador (MENARD): no es una identidad confirmada"


def _descriptions(bundle_json: dict) -> list[str]:
    return [o.get("description") or "" for o in bundle_json["objects"] if o.get("type") == "relationship"]


def test_flujo_completo_del_caso_demo(client, token, demo_case):
    # 1. Inicio de sesión real con el analista de demo; una contraseña mala se rechaza
    login = client.post("/api/auth/login", json={"username": "analista-demo", "password": DEMO_PASSWORD})
    assert login.status_code == 200, login.text
    analyst = {"Authorization": f"Bearer {login.json()['access_token']}"}
    bad = client.post("/api/auth/login", json={"username": "analista-demo", "password": "incorrecta-1234"})
    assert bad.status_code == 401

    base = f"/api/cases/{demo_case}"
    cases = client.get("/api/cases", params={"q": "FICTICIO"}, headers=analyst).json()
    assert cases["total"] == 1 and cases["items"][0]["id"] == demo_case

    # 2. Grafo del caso: nodos y aristas, con las propuestas visibles por defecto
    graph = client.get(f"{base}/graph", headers=analyst).json()
    assert graph["counts"]["nodes"] >= 45 and graph["counts"]["edges"] >= 10
    assert any(n["type"] == "malware" and n["label"] == story.MALWARE for n in graph["nodes"])

    # 3. MENARD: volver a correr no duplica hipótesis y respeta las revisiones humanas
    run = client.post(f"{base}/menard/run", headers=analyst)
    assert run.status_code == 200, run.text
    assert run.json()["accounts_analyzed"] == 33 and run.json()["links_created"] == 0
    links = client.get(f"{base}/menard/links", params={"status": "pending", "limit": 5},
                       headers=analyst).json()
    assert links["total"] >= 15
    pending = links["items"][0]
    assert pending["review_status"] == "pending" and "hipótesis" in pending["summary"].lower()

    # 4. Confirmar una hipótesis: crea la relación same_operator confirmada, con el puntaje como confianza
    confirmed = client.post(f"{base}/menard/links/{pending['id']}/review", headers=analyst,
                            json={"decision": "confirm", "note": "Revisado en la prueba de punta a punta."})
    assert confirmed.status_code == 200, confirmed.text
    relation = confirmed.json()["relation"]
    assert relation["type"] == "same_operator" and relation["status"] == "confirmed"

    # 5. Extraer IOCs de un texto: entran como propuestas, con su fuente; la técnica es solo sugerencia
    extraction = client.post(f"{base}/ioc/extract", headers=analyst, json={"text": INFRA_TEXT})
    assert extraction.status_code == 201, extraction.text
    body = extraction.json()
    assert body["persisted"]["entities_created"] >= 3
    assert "T1566.002" in [t["technique_id"] for t in body["techniques"]]
    proposed = client.get(f"{base}/entities", params={"status": "proposed", "q": "nueva-campana"},
                          headers=analyst).json()
    url = next(e for e in proposed["items"] if e["type"] == "url")
    assert url["status"] == "proposed"
    accepted = client.post(f"{base}/entities/{url['id']}/review", headers=analyst,
                           json={"decision": "accept", "note": "Confirmado por el analista."})
    assert accepted.status_code == 200 and accepted.json()["status"] == "confirmed"

    # 6. Exportar a STIX: sin pendientes por defecto, y el bundle lo acepta stix2.parse
    export = client.get(f"{base}/export/stix", headers=analyst)
    assert export.status_code == 200, export.text
    assert export.headers["x-aleph-requires-allow-custom"] == "true"  # hay x-aleph-* (billeteras, eventos)
    bundle = export.json()
    parsed = stix2.parse(json.dumps(bundle), allow_custom=True)
    assert parsed.type == "bundle" and len(parsed.objects) >= 20
    assert any(getattr(o, "type", "") == "malware" and o.name == story.MALWARE for o in parsed.objects)
    assert any(o.get("value") == "https://nueva-campana.example/pago?ref=7" for o in bundle["objects"]
               if o.get("type") == "url")
    descriptions = _descriptions(bundle)
    assert any(CONFIRMED_TEXT in d for d in descriptions)  # la hipótesis confirmada sí sale
    assert not any(PENDING_TEXT in d for d in descriptions)  # las pendientes no

    # 7. Con include_pending, las hipótesis pendientes salen, marcadas como pendientes
    with_pending = client.get(f"{base}/export/stix", params={"include_pending": "true"}, headers=analyst)
    assert with_pending.status_code == 200, with_pending.text
    assert len(with_pending.json()["objects"]) > len(bundle["objects"])
    assert any(PENDING_TEXT in d for d in _descriptions(with_pending.json()))
    stix2.parse(with_pending.text, allow_custom=True)

    # 8. El analista no puede auditar; el auditor verifica la cadena y ve las acciones del caso
    assert client.get("/api/audit/verify", headers=analyst).status_code == 403
    auditor = token("auditor-demo")
    verify = client.get("/api/audit/verify", headers=auditor)
    assert verify.status_code == 200, verify.text
    assert verify.json()["ok"] is True and verify.json()["broken_event_id"] is None
    events = client.get("/api/audit/events", params={"case_id": demo_case, "order": "asc", "limit": 500},
                        headers=auditor).json()
    actions = {e["action"] for e in events["items"]}
    assert {"menard.run", "menard.review", "ioc.extract", "entity.review", "case.export"} <= actions
    exports = [e for e in events["items"] if e["action"] == "case.export"]
    assert exports and all(e["detail"]["format"] == "stix-2.1" for e in exports)
    assert all(len(e["detail"]["sha256"]) == 64 for e in exports)
