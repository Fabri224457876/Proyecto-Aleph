import subprocess
import sys
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from aleph.api.errors import Conflict, EngineUnavailable, NotFound
from aleph.api.services import ingest_collection, run_menard
from aleph.core import audit
from aleph.core.models import (
    Account,
    AccountLink,
    AuditEvent,
    Case,
    Entity,
    Job,
    Post,
    Relation,
    Source,
    User,
)
from aleph.core.schemas import AccountProfile, AccountRecord, PostRecord


def _count(db, model):
    return db.scalar(select(func.count(model.id)))


@pytest.fixture
def case(db):
    user = User(username="worker", password_hash="x", role="analyst")
    db.add(user)
    db.flush()
    case = Case(name="Caso", legal_basis="Base legal sintética", created_by=user.id)
    db.add(case)
    db.commit()
    return case


def test_services_import_without_fastapi():
    code = "import sys, aleph.api.services as s; assert s.ingest_collection; print('fastapi' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_ingest_collection_is_idempotent(db, case, synth, tmp_path):
    result = synth.collection()
    first = ingest_collection(db, case.id, result, case.created_by, data_dir=tmp_path)
    db.commit()
    assert (first.accounts_created, first.posts_created, first.source_created) == (2, 6, True)
    assert (first.entities_created, first.relations_created, first.warnings) == (1, 1, [])
    snapshot = {m: _count(db, m) for m in (Account, Post, Entity, Relation, Source)}
    assert snapshot == {Account: 2, Post: 6, Entity: 3, Relation: 1, Source: 1}

    second = ingest_collection(db, case.id, synth.collection(), case.created_by, data_dir=tmp_path)
    db.commit()
    assert (second.accounts_created, second.accounts_updated) == (0, 2)
    assert (second.posts_created, second.posts_updated, second.posts_unchanged) == (0, 0, 6)
    assert (second.entities_created, second.entities_existing) == (0, 1)
    assert (second.relations_created, second.relations_existing) == (0, 1)
    assert second.source_created is False and second.source_id == first.source_id
    assert second.account_ids == first.account_ids
    assert {m: _count(db, m) for m in snapshot} == snapshot

    # procedencia: crudo en disco con su hash, y todo apunta a la fuente
    source = db.get(Source, first.source_id)
    assert (source.kind, source.connector, source.reference) == ("connector", "fixture", "synthetic://lote-1")
    raw = tmp_path / source.raw_path
    assert raw.is_file() and source.sha256 == first.sha256 and raw.name == f"{first.sha256}.json"
    accounts = db.execute(select(Account).order_by(Account.id)).scalars().all()
    for account in accounts:
        entity = db.get(Entity, account.entity_id)
        assert (entity.type, entity.status, entity.source_id) == ("account", "confirmed", source.id)
        assert entity.label == f"bluesky:{account.handle}" and account.source_id == source.id
    assert accounts[0].meta["aleph"]["following_handles"] == ["otra_cuenta"]
    relation = db.execute(select(Relation)).scalars().one()
    domain = db.execute(select(Entity).where(Entity.type == "domain")).scalars().one()
    assert (relation.src_id, relation.dst_id, relation.type) == (accounts[0].entity_id, domain.id, "shares_domain")
    assert relation.source_id == source.id and relation.confidence == 0.8

    events = db.execute(select(AuditEvent).where(AuditEvent.action == "collection.ingest")).scalars().all()
    assert len(events) == 2 and events[0].user_id == case.created_by
    assert audit.verify_chain(db) == (True, None)


def test_ingest_upserts_new_posts_and_edits(db, case, tmp_path):
    def result(posts, **account):
        from aleph.core.schemas import CollectionResult

        return CollectionResult(
            connector="fixture", reference="synthetic://perfil", retrieved_at=datetime(2026, 3, 5, tzinfo=UTC),
            profiles=[AccountProfile(account=AccountRecord(platform="Bluesky", **account), posts=posts)],
        )

    t0 = datetime(2026, 3, 1, 10, tzinfo=UTC)
    ingest_collection(db, case.id, result(
        [PostRecord(platform_post_id="1", text="hola", created_at=t0)], handle="@Gamma", bio="bio original", followers=5,
    ), None, data_dir=tmp_path)
    db.commit()
    summary = ingest_collection(db, case.id, result(
        [PostRecord(platform_post_id="1", text="hola (editado)", created_at=t0),
         PostRecord(platform_post_id="2", text="nuevo", created_at=t0),
         PostRecord(platform_post_id="2", text="nuevo", created_at=t0),
         PostRecord(platform_post_id=" ", text="sin id")],
        handle="gamma", bio="", followers=9,
    ), None, data_dir=tmp_path)
    db.commit()
    assert (summary.accounts_created, summary.accounts_updated) == (0, 1)
    assert (summary.posts_created, summary.posts_updated, summary.posts_unchanged) == (1, 1, 1)
    assert len(summary.warnings) == 1 and summary.source_created is True  # otro contenido, otra fuente
    account = db.execute(select(Account)).scalars().one()
    assert (account.platform, account.handle, account.bio, account.followers) == ("bluesky", "Gamma", "bio original", 9)
    assert _count(db, Post) == 2 and _count(db, Entity) == 1 and _count(db, Source) == 2
    assert db.execute(select(Post.text).where(Post.platform_post_id == "1")).scalar_one() == "hola (editado)"


def test_ingest_rejects_unknown_or_closed_case(db, case, synth, tmp_path):
    with pytest.raises(NotFound):
        ingest_collection(db, 9999, synth.collection(), None, data_dir=tmp_path)
    case.status = "closed"
    db.commit()
    with pytest.raises(Conflict):
        ingest_collection(db, case.id, synth.collection(), None, data_dir=tmp_path)
    assert _count(db, Account) == 0


def test_ingest_endpoint_and_account_listing(client, auth, make_case, synth):
    headers = auth("analyst")
    case_id = make_case()
    base = f"/api/cases/{case_id}"
    body = synth.collection(handles=("alfa", "beta", "delta"), n_posts=5).model_dump(mode="json")
    assert client.post(f"{base}/collections", json=body, headers=auth("auditor")).status_code == 403
    first = client.post(f"{base}/collections", json=body, headers=headers)
    assert first.status_code == 201 and first.json()["posts_created"] == 15
    again = client.post(f"{base}/collections", json=body, headers=headers).json()
    assert again["posts_created"] == 0 and again["accounts_created"] == 0

    accounts = client.get(f"{base}/accounts", headers=headers).json()
    assert accounts["total"] == 3 and [a["handle"] for a in accounts["items"]] == ["alfa", "beta", "delta"]
    assert all(a["post_count"] == 5 and a["entity_id"] for a in accounts["items"])
    account_id = accounts["items"][0]["id"]
    assert client.get(f"{base}/accounts/{account_id}", headers=headers).json()["display_name"] == "Alfa"
    assert client.get(f"{base}/accounts", params={"q": "delt"}, headers=headers).json()["total"] == 1

    page1 = client.get(f"{base}/accounts/{account_id}/posts", params={"limit": 2}, headers=headers).json()
    page2 = client.get(f"{base}/accounts/{account_id}/posts", params={"limit": 2, "offset": 2}, headers=headers).json()
    assert page1["total"] == 5 and len(page1["items"]) == 2 and page2["offset"] == 2
    assert [p["platform_post_id"] for p in page1["items"] + page2["items"]] == ["alfa-4", "alfa-3", "alfa-2", "alfa-1"]
    assert page1["items"][0]["created_at"].endswith(("Z", "+00:00"))
    window = client.get(
        f"{base}/accounts/{account_id}/posts", headers=headers,
        params={"since": "2026-03-01T13:00:00Z", "until": "2026-03-01T14:00:00Z", "order": "asc"},
    ).json()
    assert [p["platform_post_id"] for p in window["items"]] == ["alfa-1", "alfa-2"]


def test_menard_unavailable_returns_503(client, auth, make_case, synth, monkeypatch, db):
    headers = auth("analyst")
    case_id = make_case()
    too_few = client.post(f"/api/cases/{case_id}/menard/run", headers=headers)
    assert too_few.status_code == 409 and "al menos dos cuentas" in too_few.json()["detail"]
    client.post(f"/api/cases/{case_id}/collections", json=synth.collection().model_dump(mode="json"), headers=headers)

    monkeypatch.setitem(sys.modules, "aleph.menard", None)  # como si el paquete no estuviera instalado
    response = client.post(f"/api/cases/{case_id}/menard/run", headers=headers)
    assert response.status_code == 503
    assert "MENARD no está disponible" in response.json()["detail"]
    assert _count(db, AccountLink) == 0
    with pytest.raises(EngineUnavailable):
        run_menard(db, case_id, None)


def test_menard_engine_failure_is_reported_and_recorded(client, auth, make_case, synth, monkeypatch, db):
    import aleph.menard

    def broken(profiles):
        raise RuntimeError("sin datos suficientes")

    monkeypatch.setattr(aleph.menard, "analyze", broken, raising=False)
    headers = auth("analyst")
    case_id = make_case()
    client.post(f"/api/cases/{case_id}/collections", json=synth.collection().model_dump(mode="json"), headers=headers)
    response = client.post(f"/api/cases/{case_id}/menard/run", headers=headers)
    assert response.status_code == 502 and "sin datos suficientes" in response.json()["detail"]
    job = db.execute(select(Job)).scalars().one()
    assert (job.kind, job.status) == ("menard", "failed") and "sin datos" in job.error
    assert audit.verify_chain(db) == (True, None)


def test_menard_review_flow(client, auth, make_case, synth, menard_engine, db):
    headers = auth("analyst")
    case_id = make_case()
    base = f"/api/cases/{case_id}"
    body = synth.collection(handles=("alfa", "beta", "delta")).model_dump(mode="json")
    client.post(f"{base}/collections", json=body, headers=headers)

    run = client.post(f"{base}/menard/run", headers=headers)
    assert run.status_code == 200, run.text
    summary = run.json()
    assert (summary["accounts_analyzed"], summary["pairs_returned"], summary["links_created"]) == (3, 3, 3)
    assert summary["top"][0]["score"] == 0.91 and summary["clusters"][0]["account_ids"]
    assert "hipótesis" in summary["disclaimer"]
    # el motor recibió AccountProfile completos, reconstruidos desde la base
    (profiles,) = menard_engine
    assert [p.key for p in profiles] == ["bluesky:alfa", "bluesky:beta", "bluesky:delta"]
    assert len(profiles[0].posts) == 3 and profiles[0].posts[0].created_at.tzinfo is not None
    assert profiles[0].account.following_handles == ["otra_cuenta"] and "aleph" not in profiles[0].account.meta
    job = client.get(f"/api/jobs/{summary['job_id']}", headers=headers).json()
    assert (job["kind"], job["status"], job["result"]["links_created"]) == ("menard", "done", 3)

    links = client.get(f"{base}/menard/links", headers=headers).json()
    assert links["total"] == 3
    scores = [link["score"] for link in links["items"]]
    assert scores == sorted(scores, reverse=True) and scores[0] == 0.91
    top = links["items"][0]
    assert {top["account_a"]["handle"], top["account_b"]["handle"]} == {"alfa", "beta"}
    assert top["review_status"] == "pending" and top["confidence"] == "alta"
    assert top["signals"]["signals"][0]["evidence"][0]["description"] == "Misma muletilla"
    assert client.get(f"{base}/menard/links", params={"min_score": 0.5}, headers=headers).json()["total"] == 1
    # hasta que nadie confirma, MENARD no toca el grafo
    assert _count(db, Relation) == 1

    bad = client.post(f"{base}/menard/links/{top['id']}/review", json={"decision": "tal vez"}, headers=headers)
    assert bad.status_code == 422
    denied = client.post(f"{base}/menard/links/{top['id']}/review", json={"decision": "confirm"},
                         headers=auth("auditor"))
    assert denied.status_code == 403

    confirmed = client.post(
        f"{base}/menard/links/{top['id']}/review", headers=headers,
        json={"decision": "confirm", "note": "Coinciden muletillas y horarios."},
    )
    assert confirmed.status_code == 200, confirmed.text
    reviewed = confirmed.json()
    assert reviewed["link"]["review_status"] == "confirmed" and reviewed["link"]["reviewed_by"]
    relation = reviewed["relation"]
    assert relation["type"] == "same_operator" and relation["confidence"] == 0.91
    assert relation["status"] == "confirmed" and relation["props"]["account_link_id"] == top["id"]
    assert {relation["src_id"], relation["dst_id"]} == {top["account_a"]["entity_id"], top["account_b"]["entity_id"]}
    source = client.get(f"{base}/sources/{relation['source_id']}", headers=headers).json()
    assert source["kind"] == "menard"
    edges = client.get(f"{base}/graph", headers=headers).json()["edges"]
    assert relation["id"] in {e["id"] for e in edges}
    assert client.post(f"{base}/menard/links/{top['id']}/review", json={"decision": "confirm"},
                       headers=headers).status_code == 409

    low = links["items"][1]
    rejected = client.post(f"{base}/menard/links/{low['id']}/review", json={"decision": "reject", "note": "no"},
                           headers=headers).json()
    assert rejected["link"]["review_status"] == "rejected" and rejected["relation"] is None
    by_status = {
        status: client.get(f"{base}/menard/links", params={"status": status}, headers=headers).json()["total"]
        for status in ("pending", "confirmed", "rejected")
    }
    assert by_status == {"pending": 1, "confirmed": 1, "rejected": 1}

    # volver a correr actualiza los mismos vínculos y respeta las revisiones humanas
    rerun = client.post(f"{base}/menard/run", json={"min_score": 0.5}, headers=headers).json()
    assert (rerun["links_created"], rerun["links_updated"], rerun["links_below_min_score"]) == (0, 1, 2)
    assert _count(db, AccountLink) == 3
    assert client.get(f"{base}/menard/links/{top['id']}", headers=headers).json()["review_status"] == "confirmed"

    # retractarse de una confirmación saca la relación del grafo, sin borrarla
    undone = client.post(f"{base}/menard/links/{top['id']}/review", json={"decision": "reject"}, headers=headers).json()
    assert undone["relation"]["id"] == relation["id"] and undone["relation"]["status"] == "rejected"
    assert relation["id"] not in {e["id"] for e in client.get(f"{base}/graph", headers=headers).json()["edges"]}

    actions = [e.action for e in db.execute(select(AuditEvent).where(AuditEvent.case_id == case_id)).scalars()]
    assert actions.count("menard.run") == 2 and actions.count("menard.review") == 3
    assert "menard.links.view" in actions
    assert audit.verify_chain(db) == (True, None)
