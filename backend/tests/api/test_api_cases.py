import pytest
from sqlalchemy import select

from aleph.core import audit
from aleph.core.models import AuditEvent


def test_legal_basis_is_required(client, auth):
    headers = auth("analyst")
    missing = client.post("/api/cases", json={"name": "Sin base"}, headers=headers)
    assert missing.status_code == 422
    body = missing.json()
    assert {"field": "body.legal_basis", "message": "Campo obligatorio."} in body["errors"]
    assert body["detail"].startswith("Datos inválidos")

    for blank in ("", "   ", "\n\t"):
        response = client.post("/api/cases", json={"name": "Sin base", "legal_basis": blank}, headers=headers)
        assert response.status_code == 422, blank
        assert "base legal no puede estar vacío" in response.json()["detail"]
    assert client.post("/api/cases", json={"name": " ", "legal_basis": "Base"}, headers=headers).status_code == 422
    assert client.get("/api/cases", headers=headers).json()["total"] == 0


def test_tlp_is_validated(client, auth, synth):
    headers = auth("analyst")
    bad = client.post("/api/cases", json={"name": "X", "legal_basis": synth.legal, "tlp": "white"}, headers=headers)
    assert bad.status_code == 422 and "TLP inválido" in bad.json()["detail"]
    ok = client.post("/api/cases", json={"name": "X", "legal_basis": synth.legal, "tlp": "TLP:AMBER+STRICT"},
                     headers=headers)
    assert ok.status_code == 201 and ok.json()["tlp"] == "amber+strict"
    default = client.post("/api/cases", json={"name": "Y", "legal_basis": synth.legal}, headers=headers)
    assert default.json()["tlp"] == "amber" and default.json()["status"] == "open"
    patched = client.patch(f"/api/cases/{ok.json()['id']}", json={"tlp": "purple"}, headers=headers)
    assert patched.status_code == 422


def test_case_crud_and_lifecycle(client, auth, make_case, db):
    analyst, admin = auth("analyst"), auth("admin")
    case_id = make_case("Operación Zahir", description="caso sintético")
    make_case("Otro caso")

    listing = client.get("/api/cases", params={"q": "zahir"}, headers=analyst).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == case_id
    assert set(listing) == {"items", "total", "limit", "offset"}
    page = client.get("/api/cases", params={"limit": 1, "offset": 1}, headers=analyst).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["limit"] == 1 and page["offset"] == 1
    assert client.get("/api/cases", params={"limit": 0}, headers=analyst).status_code == 422

    detail = client.get(f"/api/cases/{case_id}", headers=analyst).json()
    assert detail["counts"]["entities"] == 0 and detail["legal_basis"]
    assert client.get("/api/cases/9999", headers=analyst).status_code == 404

    patched = client.patch(f"/api/cases/{case_id}", json={"name": "Operación Aleph"}, headers=analyst)
    assert patched.json()["name"] == "Operación Aleph"
    assert client.patch(f"/api/cases/{case_id}", json={"legal_basis": "  "}, headers=analyst).status_code == 422

    assert client.post(f"/api/cases/{case_id}/close", headers=analyst).json()["status"] == "closed"
    assert client.post(f"/api/cases/{case_id}/close", headers=analyst).status_code == 409
    # cerrado = solo lectura
    blocked = client.post(f"/api/cases/{case_id}/entities", json={"type": "person", "label": "X"}, headers=analyst)
    assert blocked.status_code == 409 and "solo lectura" in blocked.json()["detail"]
    assert client.patch(f"/api/cases/{case_id}", json={"name": "No"}, headers=analyst).status_code == 409
    assert client.get(f"/api/cases/{case_id}/graph", headers=analyst).status_code == 200

    assert client.post(f"/api/cases/{case_id}/reopen", headers=analyst).status_code == 403
    assert client.post(f"/api/cases/{case_id}/archive", headers=analyst).json()["status"] == "archived"
    assert client.get("/api/cases", params={"status": "archived"}, headers=analyst).json()["total"] == 1
    assert client.post(f"/api/cases/{case_id}/reopen", headers=admin).json()["status"] == "open"

    actions = [e.action for e in db.execute(select(AuditEvent).where(AuditEvent.case_id == case_id)).scalars()]
    for expected in ("case.create", "case.view", "case.update", "case.close", "case.archive", "case.reopen"):
        assert expected in actions
    assert audit.verify_chain(db) == (True, None)


def test_delete_only_empty_cases(client, auth, make_case):
    analyst, admin = auth("analyst"), auth("admin")
    empty, used = make_case("Vacío"), make_case("Con datos")
    client.post(f"/api/cases/{used}/entities", json={"type": "person", "label": "X"}, headers=analyst)
    assert client.delete(f"/api/cases/{empty}", headers=analyst).status_code == 403
    assert client.delete(f"/api/cases/{used}", headers=admin).status_code == 409
    assert client.delete(f"/api/cases/{empty}", headers=admin).status_code == 204
    assert client.get(f"/api/cases/{empty}", headers=admin).status_code == 404


@pytest.fixture
def two_cases(client, auth, make_case, synth, menard_engine):
    """Dos casos con datos propios; devuelve los ids de lo que pertenece al caso A."""
    headers = auth("analyst")
    a, b = make_case("Caso A"), make_case("Caso B")
    ids = {}
    for case in (a, b):
        summary = client.post(
            f"/api/cases/{case}/collections", json=synth.collection().model_dump(mode="json"), headers=headers
        ).json()
        e1 = client.post(f"/api/cases/{case}/entities", json={"type": "person", "label": "Uno"}, headers=headers).json()
        e2 = client.post(f"/api/cases/{case}/entities", json={"type": "person", "label": "Dos"}, headers=headers).json()
        rel = client.post(
            f"/api/cases/{case}/relations", json={"src_id": e1["id"], "dst_id": e2["id"], "type": "knows"},
            headers=headers,
        ).json()
        run = client.post(f"/api/cases/{case}/menard/run", headers=headers).json()
        finding = client.post(f"/api/cases/{case}/findings", headers=headers, json={"chunk": {
            "kind": "email", "value": f"caso{case}@ejemplo.test", "page_url": "https://ejemplo.test/p",
            "captured_at": "2026-03-01T10:00:00Z",
        }}).json()
        section = client.get(f"/api/cases/{case}/sections", headers=headers).json()[0]
        ids[case] = {
            "entity": e1["id"], "entity2": e2["id"], "relation": rel["id"], "source": summary["source_id"],
            "account": summary["account_ids"][0], "link": run["top"][0]["link_id"],
            "finding": finding["id"], "section": section["id"],
        }
    return a, b, ids[a], ids[b]


def test_case_isolation(client, auth, two_cases):
    """Nada del caso A se lee ni se modifica por id desde el caso B."""
    headers = auth("analyst")
    a, b, own, other = two_cases
    base = f"/api/cases/{b}"

    reads = [
        f"/entities/{own['entity']}", f"/entities/{own['entity']}/neighbors", f"/relations/{own['relation']}",
        f"/sources/{own['source']}", f"/sources/{own['source']}/raw", f"/accounts/{own['account']}",
        f"/accounts/{own['account']}/posts", f"/menard/links/{own['link']}", f"/findings/{own['finding']}",
        f"/graph/path?source={own['entity']}&target={other['entity']}",
        f"/graph/path?source={other['entity']}&target={own['entity2']}",
        f"/findings?section_id={own['section']}",
    ]
    for path in reads:
        response = client.get(base + path, headers=headers)
        assert response.status_code == 404, (path, response.text)

    writes = [
        ("patch", f"/entities/{own['entity']}", {"label": "pisada"}),
        ("delete", f"/entities/{own['entity']}", None),
        ("post", f"/entities/{own['entity']}/review", {"decision": "reject"}),
        ("patch", f"/relations/{own['relation']}", {"type": "pisada"}),
        ("delete", f"/relations/{own['relation']}", None),
        ("post", f"/relations/{own['relation']}/review", {"decision": "accept"}),
        ("post", "/relations", {"src_id": other["entity"], "dst_id": own["entity"], "type": "cruza"}),
        ("post", "/relations", {"src_id": own["entity"], "dst_id": own["entity2"], "type": "cruza"}),
        ("post", "/entities", {"type": "person", "label": "X", "source_id": own["source"]}),
        ("post", "/entities/merge", {"keep_id": other["entity"], "duplicate_id": own["entity"]}),
        ("post", "/entities/merge", {"keep_id": own["entity"], "duplicate_id": other["entity"]}),
        ("post", f"/menard/links/{own['link']}/review", {"decision": "confirm"}),
        ("patch", f"/findings/{own['finding']}", {"note": "pisada"}),
        ("patch", f"/findings/{other['finding']}", {"section_id": own["section"]}),
        ("delete", f"/findings/{own['finding']}", None),
        ("patch", f"/sections/{own['section']}", {"name": "pisada"}),
        ("delete", f"/sections/{own['section']}", None),
        ("post", "/findings", {"section_id": own["section"], "chunk": {
            "kind": "text", "value": "x", "page_url": "https://ejemplo.test", "captured_at": "2026-03-01T10:00:00Z"}}),
        ("post", "/findings", {"attach_to_entity_id": own["entity"], "chunk": {
            "kind": "text", "value": "x", "page_url": "https://ejemplo.test", "captured_at": "2026-03-01T10:00:00Z"}}),
    ]
    for method, path, body in writes:
        response = client.request(method, base + path, json=body, headers=headers)
        assert response.status_code == 404, (method, path, response.text)
    bad_run = client.post(f"{base}/menard/run", json={"account_ids": [own["account"]]}, headers=headers)
    assert bad_run.status_code == 400

    # El caso A quedó intacto y cada listado solo muestra lo propio
    assert client.get(f"/api/cases/{a}/entities/{own['entity']}", headers=headers).json()["label"] == "Uno"
    assert client.get(f"/api/cases/{a}/relations/{own['relation']}", headers=headers).json()["type"] == "knows"
    assert client.get(f"/api/cases/{a}/findings/{own['finding']}", headers=headers).json()["note"] == ""
    link = client.get(f"/api/cases/{a}/menard/links/{own['link']}", headers=headers).json()
    assert link["review_status"] == "pending"
    for path in ("entities", "relations", "sources", "accounts", "findings", "menard/links"):
        mine = client.get(f"/api/cases/{a}/{path}", headers=headers).json()["items"]
        theirs = client.get(f"/api/cases/{b}/{path}", headers=headers).json()["items"]
        assert mine and theirs and all(item["case_id"] == a for item in mine), path
        assert not {i["id"] for i in mine} & {i["id"] for i in theirs}, path
    graph_b = client.get(f"/api/cases/{b}/graph", headers=headers).json()
    assert own["entity"] not in {n["id"] for n in graph_b["nodes"]}
    search_b = client.get(f"/api/cases/{b}/search", params={"q": "Uno"}, headers=headers).json()
    assert [e["id"] for e in search_b["entities"]] == [other["entity"]]
