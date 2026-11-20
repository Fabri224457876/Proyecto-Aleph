"""Integridad referencial: borrar o fusionar entidades con datos que cuelgan de ellas.

Las pruebas corren en SQLite con claves foráneas activas, como PostgreSQL en producción.
"""


from aleph.core.models import Entity, Finding


def _chunk(kind, value):
    return {"kind": kind, "value": value, "quote": f"«{value}»", "context": f"dato {value}",
            "page_url": "https://foro.ejemplo.test/hilo/1", "page_title": "Hilo", "platform": "generic",
            "captured_at": "2026-03-02T15:00:00Z"}


def _case(client, auth, make_case):
    case_id = make_case()
    return f"/api/cases/{case_id}", auth("analyst")


def test_delete_entity_with_finding(client, auth, make_case, db):
    base, headers = _case(client, auth, make_case)
    finding = client.post(f"{base}/findings", headers=headers, json={"chunk": _chunk("email", "a@b.test")}).json()
    response = client.delete(f"{base}/entities/{finding['entity_id']}", headers=headers)
    assert response.status_code == 204, response.text
    kept = db.get(Finding, finding["id"])
    db.refresh(kept)
    assert kept is not None and kept.entity_id is None


def test_merge_entity_with_finding(client, auth, make_case, db):
    base, headers = _case(client, auth, make_case)
    keep = client.post(f"{base}/entities", json={"type": "email", "label": "a@b.test"}, headers=headers).json()
    dup = client.post(f"{base}/entities", json={"type": "email", "label": "otro@b.test"}, headers=headers).json()
    finding = client.post(f"{base}/findings", headers=headers, json={
        "chunk": _chunk("text", "cita"), "attach_to_entity_id": dup["id"]}).json()
    assert finding["entity_id"] == dup["id"]
    response = client.post(f"{base}/entities/merge", json={"keep_id": keep["id"], "duplicate_id": dup["id"]},
                           headers=headers)
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(Finding, finding["id"]).entity_id == keep["id"]
    assert db.get(Entity, dup["id"]) is None
