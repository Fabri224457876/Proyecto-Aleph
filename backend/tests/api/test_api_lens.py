import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from aleph.api import configure_app
from aleph.core import audit
from aleph.core.models import Account, AuditEvent, Entity, Post, Relation, Source
from aleph.core.schemas import DEFAULT_SECTIONS

CAPTURED = "2026-03-02T15:00:00Z"


def _batch(synth, **extra):
    body = {
        "page_url": "https://red.ejemplo.test/alfa", "page_title": "alfa en Red", "platform": "bluesky",
        "captured_at": CAPTURED, "extension_version": "0.1.0",
        "profiles": [synth.profile("alfa", 3).model_dump(mode="json"), synth.profile("beta", 2).model_dump(mode="json")],
        "interactions": [["alfa", "@Beta", "replies_to"], ["alfa", "gamma", "mentions"]],
        "text_snippets": ["contacto: alfa@ejemplo.test"],
    }
    body.update(extra)
    return body


@pytest.fixture
def lens_case(client, auth, make_case):
    return f"/api/cases/{make_case()}", auth("analyst")


def _count(db, model):
    return db.scalar(select(func.count(model.id)))


def test_capture_is_idempotent_and_stores_interactions(client, lens_case, auth, synth, db, tmp_path):
    base, headers = lens_case
    assert client.post(f"{base}/captures", json=_batch(synth), headers=auth("auditor")).status_code == 403
    first = client.post(f"{base}/captures", json=_batch(synth), headers=headers)
    assert first.status_code == 201, first.text
    summary = first.json()
    assert (summary["accounts_new"], summary["accounts_known"]) == (3, 0)  # gamma entra por la interacción
    assert (summary["posts_new"], summary["posts_known"]) == (5, 0)
    assert (summary["relations_new"], summary["source_created"]) == (2, True)

    source = db.get(Source, summary["source_id"])
    assert (source.kind, source.connector, source.reference) == ("connector", "lens", "https://red.ejemplo.test/alfa")
    assert source.retrieved_at.isoformat().startswith("2026-03-02T15:00:00") and len(source.sha256) == 64
    assert "alfa@ejemplo.test" in (tmp_path / "data" / source.raw_path).read_text(encoding="utf-8")
    entity_of = {a.handle: a.entity_id for a in db.execute(select(Account)).scalars()}
    edges = {(r.src_id, r.dst_id, r.type) for r in db.execute(select(Relation)).scalars()}
    assert edges == {
        (entity_of["alfa"], entity_of["beta"], "replies_to"), (entity_of["alfa"], entity_of["gamma"], "mentions"),
    }

    # la extensión reenvía lo ya visto (más tarde) y agrega una publicación nueva
    again = client.post(f"{base}/captures", json=_batch(synth, captured_at="2026-03-02T15:05:00Z"), headers=headers).json()
    assert (again["accounts_new"], again["accounts_known"], again["posts_new"], again["posts_known"]) == (0, 3, 0, 5)
    assert (again["relations_new"], again["relations_known"], again["source_created"]) == (0, 2, False)
    more = _batch(synth, profiles=[synth.profile("alfa", 4).model_dump(mode="json")], interactions=[])
    grown = client.post(f"{base}/captures", json=more, headers=headers).json()
    assert (grown["accounts_new"], grown["posts_new"], grown["posts_known"]) == (0, 1, 3)
    assert (_count(db, Account), _count(db, Post), _count(db, Relation), _count(db, Source)) == (3, 6, 2, 2)

    assert client.post(f"{base}/captures", json=_batch(synth, page_url=" "), headers=headers).status_code == 400
    assert client.post(f"{base}/captures", json={"platform": "x"}, headers=headers).status_code == 422
    assert audit.verify_chain(db) == (True, None)


def test_lookup_is_batched(client, lens_case, auth, synth, menard_engine, session_factory, db):
    base, headers = lens_case
    client.post(f"{base}/captures", json=_batch(synth), headers=headers)
    run = client.post(f"{base}/menard/run", headers=headers).json()
    client.post(f"{base}/findings", headers=headers, json={"note": "ojo con esta cuenta", "chunk": {
        "kind": "account", "value": "@alfa", "platform": "bluesky", "page_url": "https://red.ejemplo.test/alfa",
        "captured_at": CAPTURED}})

    statements = []
    engine = session_factory.kw["bind"]
    listener = lambda conn, cursor, statement, *rest: statements.append(statement)

    def lookup(handles, role="analyst"):
        statements.clear()
        event.listen(engine, "before_cursor_execute", listener)
        try:
            response = client.post(f"{base}/captures/lookup", headers=auth(role),
                                   json={"platform": "Bluesky", "handles": handles})
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        assert response.status_code == 200, response.text
        return response.json()["handles"], len(statements)

    few, queries_few = lookup(["@Alfa", "beta", "nadie"])
    assert set(few) == {"@Alfa", "beta", "nadie"}
    assert few["nadie"] == {"known": False, "entity_id": None, "posts_captured": 0, "links": [], "relations": [],
                            "note": ""}
    alfa = few["@Alfa"]
    assert alfa["known"] is True and alfa["posts_captured"] == 3 and alfa["note"] == "ojo con esta cuenta"
    assert {(link["other"], link["score"], link["review_status"]) for link in alfa["links"]} == {
        ("beta", 0.91, "pending"), ("gamma", 0.12, "pending")}
    assert run["top"][0]["link_id"] in {link["link_id"] for link in alfa["links"]}
    assert {(r["type"], r["label"], r["direction"]) for r in alfa["relations"]} == {
        ("replies_to", "bluesky:beta", "out"), ("mentions", "bluesky:gamma", "out")}
    assert few["beta"]["relations"][0]["direction"] == "in" and few["beta"]["posts_captured"] == 2

    many, queries_many = lookup(["alfa", "beta", "gamma"] + [f"desconocido{i}" for i in range(200)], role="auditor")
    assert len(many) == 203 and sum(1 for info in many.values() if info["known"]) == 3
    # la cantidad de consultas no crece con los handles (incluye usuario, caso, y evento de auditoría con su savepoint)
    assert queries_many <= queries_few <= 14
    none, _ = lookup([])
    assert none == {}
    other_platform = client.post(f"{base}/captures/lookup", headers=headers,
                                 json={"platform": "x", "handles": ["alfa"]}).json()
    assert other_platform["handles"]["alfa"]["known"] is False
    too_many = client.post(f"{base}/captures/lookup", headers=headers,
                           json={"platform": "x", "handles": [str(i) for i in range(501)]})
    assert too_many.status_code == 400
    assert client.post(f"{base}/captures/lookup", json={"platform": "x", "handles": []}).status_code == 401


def test_sections(client, lens_case, auth, db):
    base, headers = lens_case
    sections = client.get(f"{base}/sections", headers=auth("auditor")).json()
    assert [s["name"] for s in sections] == list(DEFAULT_SECTIONS)
    assert [s["position"] for s in sections] == list(range(len(DEFAULT_SECTIONS)))
    assert all(s["findings"] == 0 for s in sections)

    created = client.post(f"{base}/sections", json={"name": " Finanzas "}, headers=headers)
    assert created.status_code == 201 and created.json()["name"] == "Finanzas"
    assert created.json()["position"] == len(DEFAULT_SECTIONS)
    assert client.post(f"{base}/sections", json={"name": "finanzas"}, headers=headers).status_code == 409
    assert client.post(f"{base}/sections", json={"name": "  "}, headers=headers).status_code == 422
    assert client.post(f"{base}/sections", json={"name": "X"}, headers=auth("auditor")).status_code == 403

    section_id = created.json()["id"]
    renamed = client.patch(f"{base}/sections/{section_id}", json={"name": "Dinero", "position": 0}, headers=headers)
    assert renamed.json() == {"id": section_id, "name": "Dinero", "position": 0, "findings": 0}
    assert client.patch(f"{base}/sections/{section_id}", json={"name": "identidad"}, headers=headers).status_code == 409
    assert client.patch(f"{base}/sections/9999", json={"name": "X"}, headers=headers).status_code == 404
    assert client.get(f"{base}/sections", headers=headers).json()[0]["name"] in ("Identidad", "Dinero")


def test_sections_are_created_lazily_for_older_cases(client, auth, users, db):
    from aleph.core.models import Case

    case = Case(name="Anterior a los incisos", legal_basis="Base legal sintética", created_by=users["analyst"])
    db.add(case)
    db.commit()
    sections = client.get(f"/api/cases/{case.id}/sections", headers=auth("auditor")).json()
    assert [s["name"] for s in sections] == list(DEFAULT_SECTIONS)
    assert client.get(f"/api/cases/{case.id}/sections", headers=auth("auditor")).json() == sections
    actions = [e.action for e in db.execute(select(AuditEvent)).scalars()]
    assert actions.count("section.defaults") == 1
    assert audit.verify_chain(db) == (True, None)


def _chunk(kind, value, **extra):
    return {"kind": kind, "value": value, "quote": f"«{value}»", "context": f"escribime a {value} por favor",
            "page_url": "https://foro.ejemplo.test/hilo/7", "page_title": "Hilo 7", "platform": "generic",
            "captured_at": CAPTURED, **extra}


def test_findings_create_entities_relations_and_notes(client, lens_case, auth, db):
    base, headers = lens_case
    sections = {s["name"]: s["id"] for s in client.get(f"{base}/sections", headers=headers).json()}
    person = client.post(f"{base}/entities", json={"type": "person", "label": "Ana Sintética"}, headers=headers).json()

    # 1. un email soltado en "Contactos": crea fuente, entidad confirmada y hallazgo
    first = client.post(f"{base}/findings", headers=headers, json={
        "section_id": sections["Contactos"], "chunk": _chunk("email", "Ana@Ejemplo.test"), "note": "del foro"})
    assert first.status_code == 201, first.text
    finding = first.json()
    assert finding["entity_created"] is True and finding["relation_id"] is None
    assert (finding["kind"], finding["value"], finding["section_id"]) == ("email", "Ana@Ejemplo.test", sections["Contactos"])
    assert finding["chunk"]["page_title"] == "Hilo 7" and finding["note"] == "del foro"
    source = db.get(Source, finding["source_id"])
    expected_hash = hashlib.sha256("«Ana@Ejemplo.test»\nescribime a Ana@Ejemplo.test por favor".encode()).hexdigest()
    assert (source.reference, source.sha256, source.connector) == ("https://foro.ejemplo.test/hilo/7", expected_hash, "lens")
    assert source.retrieved_at.isoformat().startswith("2026-03-02T15:00:00")
    entity = db.get(Entity, finding["entity_id"])
    assert (entity.type, entity.label, entity.status, entity.source_id) == ("email", "Ana@Ejemplo.test", "confirmed", source.id)

    # 2. el mismo email soltado sobre una persona: reutiliza la entidad y crea la relación
    second = client.post(f"{base}/findings", headers=headers, json={
        "chunk": _chunk("email", "ana@ejemplo.test"), "attach_to_entity_id": person["id"]}).json()
    assert second["entity_id"] == finding["entity_id"] and second["entity_created"] is False
    assert second["section_id"] == sections["Sin clasificar"]  # sin inciso = sin clasificar
    relation = db.get(Relation, second["relation_id"])
    assert (relation.src_id, relation.dst_id, relation.type, relation.status) == (
        person["id"], finding["entity_id"], "related_to", "confirmed")
    assert relation.source_id == second["source_id"] and relation.props["finding_id"] == second["id"]
    third = client.post(f"{base}/findings", headers=headers, json={
        "chunk": _chunk("email", "ana@ejemplo.test"), "attach_to_entity_id": person["id"]}).json()
    assert third["relation_id"] == second["relation_id"] and _count(db, Relation) == 1

    # 3. una cita de texto soltada sobre la persona queda como nota suya, sin entidad nueva
    quote = client.post(f"{base}/findings", headers=headers, json={
        "section_id": sections["Actividad"], "attach_to_entity_id": person["id"],
        "chunk": _chunk("text", "dijo que viaja el martes", detected_by="manual"), "note": "posible viaje"}).json()
    assert quote["entity_id"] == person["id"] and quote["relation_id"] is None and quote["entity_created"] is False
    loose = client.post(f"{base}/findings", headers=headers, json={"chunk": _chunk("text", "cita suelta")}).json()
    assert loose["entity_id"] is None
    assert _count(db, Entity) == 2

    # 4. una entidad propuesta por la IA queda confirmada cuando una persona la incorpora
    proposed = client.post(f"{base}/entities", headers=headers,
                           json={"type": "domain", "label": "ejemplo.test", "status": "proposed"}).json()
    promoted = client.post(f"{base}/findings", headers=headers, json={"chunk": _chunk("domain", "EJEMPLO.test")}).json()
    assert promoted["entity_id"] == proposed["id"]
    assert client.get(f"{base}/entities/{proposed['id']}", headers=headers).json()["status"] == "confirmed"

    # validaciones
    for bad in ({"chunk": _chunk("nave", "x")}, {"chunk": _chunk("email", "  ")},
                {"chunk": _chunk("email", "a@b.test", page_url="")}):
        assert client.post(f"{base}/findings", json=bad, headers=headers).status_code == 400, bad
    assert client.post(f"{base}/findings", json={"chunk": {"kind": "email"}}, headers=headers).status_code == 422
    assert client.post(f"{base}/findings", json={"chunk": _chunk("email", "a@b.test"), "section_id": 9999},
                       headers=headers).status_code == 404
    assert client.post(f"{base}/findings", json={"chunk": _chunk("email", "a@b.test")},
                       headers=auth("auditor")).status_code == 403

    # listado, filtros y contadores
    counts = {s["name"]: s["findings"] for s in client.get(f"{base}/sections", headers=headers).json()}
    assert counts == {**dict.fromkeys(DEFAULT_SECTIONS, 0), "Contactos": 1, "Actividad": 1, "Sin clasificar": 4}
    everything = client.get(f"{base}/findings", headers=auth("auditor")).json()
    assert everything["total"] == 6 and everything["items"][0]["id"] > everything["items"][-1]["id"]
    in_contacts = client.get(f"{base}/findings", params={"section_id": sections["Contactos"]}, headers=headers).json()
    assert [f["id"] for f in in_contacts["items"]] == [finding["id"]]
    assert client.get(f"{base}/findings", params={"unclassified": "true"}, headers=headers).json()["total"] == 4
    assert client.get(f"{base}/findings", params={"entity_id": person["id"]}, headers=headers).json()["total"] == 1
    assert client.get(f"{base}/findings", params={"kind": "email", "limit": 2}, headers=headers).json()["total"] == 3

    # mover, anotar, borrar inciso (los hallazgos no se pierden) y borrar hallazgo
    moved = client.patch(f"{base}/findings/{loose['id']}", headers=headers,
                         json={"section_id": sections["Actividad"], "note": "revisar"}).json()
    assert (moved["section_id"], moved["note"]) == (sections["Actividad"], "revisar")
    assert client.patch(f"{base}/findings/{loose['id']}", json={"section_id": 9999}, headers=headers).status_code == 404
    assert client.delete(f"{base}/sections/{sections['Actividad']}", headers=headers).status_code == 204
    for moved_id in (quote["id"], loose["id"]):
        after = client.get(f"{base}/findings/{moved_id}", headers=headers).json()
        assert after["section_id"] == sections["Sin clasificar"]
    assert client.delete(f"{base}/sections/{sections['Sin clasificar']}", headers=headers).status_code == 204
    assert client.get(f"{base}/findings/{loose['id']}", headers=headers).json()["section_id"] is None
    assert client.get(f"{base}/findings", headers=headers).json()["total"] == 6
    unset = client.patch(f"{base}/findings/{finding['id']}", json={"section_id": None}, headers=headers).json()
    assert unset["section_id"] is None

    assert client.delete(f"{base}/findings/{finding['id']}", headers=headers).status_code == 204
    assert client.get(f"{base}/findings/{finding['id']}", headers=headers).status_code == 404
    assert db.get(Source, finding["source_id"]) is not None and db.get(Entity, finding["entity_id"]) is not None

    actions = [e.action for e in db.execute(select(AuditEvent)).scalars()]
    assert actions.count("finding.create") == 6 and actions.count("section.delete") == 2
    assert "finding.update" in actions and "finding.delete" in actions
    assert audit.verify_chain(db) == (True, None)


def test_account_finding_is_adopted_by_later_capture(client, lens_case, synth, db):
    """Un handle arrastrado a mano y después capturado no deja dos entidades para la misma cuenta."""
    base, headers = lens_case
    dropped = client.post(f"{base}/findings", headers=headers, json={
        "chunk": _chunk("account", "@Alfa", platform="bluesky")}).json()
    entity = db.get(Entity, dropped["entity_id"])
    assert (entity.type, entity.label, entity.props["handle"]) == ("account", "bluesky:Alfa", "Alfa")
    client.post(f"{base}/captures", json=_batch(synth, interactions=[]), headers=headers)
    alfa = db.execute(select(Account).where(Account.handle == "alfa")).scalars().one()
    assert alfa.entity_id == entity.id
    assert db.scalar(select(func.count(Entity.id)).where(Entity.type == "account")) == 2
    again = client.post(f"{base}/findings", headers=headers, json={
        "chunk": _chunk("account", "alfa", platform="bluesky")}).json()
    assert again["entity_id"] == entity.id and again["entity_created"] is False


def test_configure_app_cors(monkeypatch):
    monkeypatch.delenv("ALEPH_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("ALEPH_CORS_ORIGIN_REGEX", raising=False)
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    assert configure_app(app) is app
    client = TestClient(app)
    extension = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"

    def preflight(origin):
        return client.options("/ping", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"})

    for origin in (extension, "http://localhost:5173", "http://127.0.0.1:8101"):
        response = preflight(origin)
        assert response.status_code == 200 and response.headers["access-control-allow-origin"] == origin
        assert "authorization" in response.headers["access-control-allow-headers"].lower()
    assert client.get("/ping", headers={"Origin": extension}).headers["access-control-allow-origin"] == extension
    for origin in ("https://malicioso.test", "http://localhost.malicioso.test", "chrome-extension://corto",
                   "https://sub.chrome-extension://abcdefghijklmnopabcdefghijklmnop"):
        response = preflight(origin)
        assert response.status_code == 400 and "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in preflight(extension).headers

    custom = FastAPI()
    configure_app(custom, origins=["https://aleph.organismo.test"], origin_regex="")
    custom_client = TestClient(custom)
    allowed = custom_client.options("/x", headers={
        "Origin": "https://aleph.organismo.test", "Access-Control-Request-Method": "GET"})
    assert allowed.headers["access-control-allow-origin"] == "https://aleph.organismo.test"
    assert custom_client.options("/x", headers={
        "Origin": extension, "Access-Control-Request-Method": "GET"}).status_code == 400
