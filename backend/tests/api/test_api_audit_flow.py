import threading

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from aleph.api import auditlog
from aleph.core import audit
from aleph.core.db import init_db, make_engine
from aleph.core.models import AuditEvent


def test_full_flow_keeps_audit_chain_valid(client, auth, users, synth, menard_engine, db):
    """Caso -> fuente -> recolección -> grafo -> MENARD -> revisión -> exportación; la cadena verifica."""
    analyst, auditor, admin = auth("analyst"), auth("auditor"), auth("admin")
    assert client.post("/api/auth/login", json={"username": "analyst", "password": synth.password}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "analyst", "password": "incorrecta-1"}).status_code == 401

    case_id = client.post("/api/cases", json={"name": "Flujo", "legal_basis": synth.legal}, headers=analyst).json()["id"]
    base = f"/api/cases/{case_id}"
    client.post(f"{base}/sources/upload", headers=analyst, files={"file": ("nota.txt", b"evidencia sintetica")})
    client.post(f"{base}/collections", json=synth.collection().model_dump(mode="json"), headers=analyst)
    event = client.post(f"{base}/entities", headers=analyst, json={
        "type": "event", "label": "Reunión sintética", "props": {"date": "2026-03-01T13:30:00Z"}}).json()
    person = client.post(f"{base}/entities", headers=analyst,
                         json={"type": "person", "label": "Ñandú Pérez", "status": "proposed"}).json()
    client.post(f"{base}/entities/{person['id']}/review", json={"decision": "accept"}, headers=analyst)
    client.post(f"{base}/relations", headers=analyst,
                json={"src_id": person["id"], "dst_id": event["id"], "type": "attended", "confidence": 0.7})
    client.get(base, headers=auditor)
    client.get(f"{base}/graph", headers=auditor)
    run = client.post(f"{base}/menard/run", headers=analyst).json()
    client.post(f"{base}/menard/links/{run['top'][0]['link_id']}/review", headers=analyst,
                json={"decision": "confirm", "note": "revisado"})
    client.post(f"{base}/entities", json={"type": "person", "label": "X"}, headers=auditor)  # denegado
    export = client.get(f"{base}/export", params={"include_posts": "true"}, headers=auditor)
    assert export.status_code == 200
    exported = export.json()
    assert len(exported["posts"]) == 6 and len(exported["accounts"]) == 2 and exported["case"]["id"] == case_id
    assert exported["account_links"][0]["review_status"] == "confirmed"
    assert any(r["type"] == "same_operator" for r in exported["relations"])
    client.post(f"{base}/close", headers=analyst)

    assert client.get("/api/audit/verify", headers=analyst).status_code == 403
    verify = client.get("/api/audit/verify", headers=auditor)
    assert verify.status_code == 200, verify.text
    report = verify.json()
    assert report["ok"] is True and report["broken_event_id"] is None and report["total_events"] >= 15
    assert audit.verify_chain(db) == (True, None)

    events = client.get("/api/audit/events", params={"limit": 500, "order": "asc"}, headers=auditor).json()
    actions = [e["action"] for e in events["items"]]
    for expected in (
        "auth.login", "auth.login.failed", "case.create", "source.upload", "collection.ingest", "entity.create",
        "entity.review", "relation.create", "case.view", "graph.view", "menard.run", "menard.review",
        "auth.denied", "case.export", "case.close", "audit.verify",
    ):
        assert expected in actions, expected
    assert events["total"] == len(actions) and events["items"][0]["prev_hash"] == "0" * 64
    assert all(b["prev_hash"] == a["hash"] for a, b in zip(events["items"], events["items"][1:], strict=False))
    create = next(e for e in events["items"] if e["action"] == "case.create")
    assert create["user_id"] == users["analyst"] and create["case_id"] == case_id
    assert create["detail"]["legal_basis"] == synth.legal

    by_case = client.get("/api/audit/events", params={"case_id": case_id, "action": "menard."}, headers=admin).json()
    assert {e["action"] for e in by_case["items"]} == {"menard.run", "menard.review"}
    by_user = client.get("/api/audit/events", params={"user_id": users["auditor"], "action": "auth.denied"},
                         headers=admin).json()
    assert by_user["total"] == 1
    paged = client.get("/api/audit/events", params={"limit": 3, "offset": 2}, headers=auditor).json()
    assert len(paged["items"]) == 3 and paged["offset"] == 2

    # Alterar un evento rompe la cadena y el endpoint lo informa
    victim = db.execute(select(AuditEvent).where(AuditEvent.action == "menard.review")).scalars().first()
    victim.detail = {**victim.detail, "decision": "reject"}
    db.commit()
    broken = client.get("/api/audit/verify", headers=auditor).json()
    assert broken["ok"] is False and broken["broken_event_id"] == victim.id
    assert str(victim.id) in broken["detail"]


def test_timeline_merges_posts_and_events(client, auth, make_case, synth):
    headers = auth("analyst")
    case_id = make_case()
    base = f"/api/cases/{case_id}"
    client.post(f"{base}/collections", json=synth.collection(handles=("alfa",)).model_dump(mode="json"), headers=headers)

    def event(label, **props):
        return client.post(f"{base}/entities", headers=headers,
                           json={"type": "event", "label": label, "props": props}).json()["id"]

    middle = event("Entre publicaciones", date="2026-03-01T12:30:00+00:00", description="detalle")
    event("Antes de todo", fecha="2026-02-27")
    event("Sin fecha")
    rejected = client.post(f"{base}/entities", headers=headers, json={
        "type": "event", "label": "Propuesto y rechazado", "status": "proposed", "props": {"date": "2026-03-01"}}).json()
    client.post(f"{base}/entities/{rejected['id']}/review", json={"decision": "reject"}, headers=headers)

    timeline = client.get(f"{base}/timeline", headers=auth("auditor")).json()
    assert timeline["total"] == 5 and timeline["undated_events"] == 1
    assert [i["title"] for i in timeline["items"]] == [
        "Antes de todo", "alfa (bluesky)", "Entre publicaciones", "alfa (bluesky)", "alfa (bluesky)",
    ]
    stamps = [i["at"] for i in timeline["items"]]
    assert stamps == sorted(stamps)
    item = timeline["items"][2]
    assert (item["kind"], item["entity_id"], item["text"]) == ("event", middle, "detalle")
    assert timeline["items"][1]["kind"] == "post" and timeline["items"][1]["handle"] == "alfa"

    desc = client.get(f"{base}/timeline", params={"order": "desc", "limit": 2}, headers=headers).json()
    assert [i["title"] for i in desc["items"]] == ["alfa (bluesky)", "alfa (bluesky)"] and desc["total"] == 5
    only_events = client.get(f"{base}/timeline", params={"kind": "event"}, headers=headers).json()
    assert [i["kind"] for i in only_events["items"]] == ["event", "event"]
    window = client.get(f"{base}/timeline", headers=headers,
                        params={"since": "2026-03-01T12:15:00Z", "until": "2026-03-01T13:00:00Z"}).json()
    assert [i["title"] for i in window["items"]] == ["Entre publicaciones", "alfa (bluesky)"]


def test_jobs_listing(client, auth, make_case, synth, menard_engine):
    headers = auth("analyst")
    first, second = make_case("Uno"), make_case("Dos")
    for case_id in (first, second):
        client.post(f"/api/cases/{case_id}/collections", json=synth.collection().model_dump(mode="json"),
                    headers=headers)
        client.post(f"/api/cases/{case_id}/menard/run", headers=headers)
    assert client.get("/api/jobs").status_code == 401
    jobs = client.get("/api/jobs", headers=auth("auditor")).json()
    assert jobs["total"] == 2 and jobs["items"][0]["id"] > jobs["items"][1]["id"]
    mine = client.get("/api/jobs", params={"case_id": first, "kind": "menard", "status": "done"}, headers=headers).json()
    assert mine["total"] == 1 and mine["items"][0]["case_id"] == first
    assert client.get("/api/jobs", params={"status": "failed"}, headers=headers).json()["total"] == 0
    assert client.get("/api/jobs/9999", headers=headers).status_code == 404


def test_concurrent_writes_do_not_fork_the_chain(tmp_path):
    """Sin serializar, dos pedidos simultáneos leerían el mismo `prev_hash` y la cadena se rompería."""
    engine = make_engine(f"sqlite:///{(tmp_path / 'audit.db').as_posix()}")
    init_db(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    errors = []
    start = threading.Barrier(8)

    def worker(n: int) -> None:
        try:
            start.wait(timeout=10)
            for i in range(10):
                with factory() as session:
                    auditlog.record(session, "test.concurrent", user_id=n, detail={"i": i, "when": None})
                    session.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors
    with factory() as session:
        assert len(session.execute(select(AuditEvent)).scalars().all()) == 80
        assert audit.verify_chain(session) == (True, None)
    assert not auditlog._chain_lock.locked()
    engine.dispose()


def test_lock_is_released_on_rollback_and_error(db):
    auditlog.record(db, "test.rollback", detail={"tuple": (1, 2), "n": 1.5})
    assert auditlog._chain_lock.locked()
    db.rollback()
    assert not auditlog._chain_lock.locked()
    auditlog.record(db, "test.detail", detail={"tuple": (1, 2), 3: "clave numérica", "texto": "ñandú"})
    db.commit()
    assert not auditlog._chain_lock.locked()
    db.expire_all()
    assert audit.verify_chain(db) == (True, None)  # el detalle sobrevive al ida y vuelta por JSON


def test_app_starts_and_documents_the_api(client):
    assert client.get("/health").json() == {"status": "ok"}
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    for path in (
        "/api/auth/login", "/api/cases", "/api/cases/{case_id}/graph", "/api/cases/{case_id}/graph/path",
        "/api/cases/{case_id}/entities/merge", "/api/cases/{case_id}/sources/upload",
        "/api/cases/{case_id}/menard/run", "/api/cases/{case_id}/menard/links/{link_id}/review",
        "/api/cases/{case_id}/timeline", "/api/cases/{case_id}/captures", "/api/cases/{case_id}/captures/lookup",
        "/api/cases/{case_id}/sections", "/api/cases/{case_id}/findings", "/api/jobs", "/api/audit/verify",
    ):
        assert path in paths, path
    create = paths["/api/cases"]["post"]
    assert "legal_basis" in schema["components"]["schemas"]["CaseCreate"]["required"]
    assert create["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("ValidationErrorOut")
    assert schema["components"]["schemas"]["CaseCreate"]["properties"]["tlp"]["enum"][0] == "clear"
