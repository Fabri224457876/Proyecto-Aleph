import hashlib

import pytest

from aleph.core import audit


@pytest.fixture
def graph(client, auth, make_case):
    """Cadena a - b - c - d, más un nodo suelto y uno propuesto."""
    headers = auth("analyst")
    case_id = make_case()
    base = f"/api/cases/{case_id}"

    def entity(type_, label, **extra):
        response = client.post(f"{base}/entities", json={"type": type_, "label": label, **extra}, headers=headers)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def relation(src, dst, type_="linked", **extra):
        response = client.post(
            f"{base}/relations", json={"src_id": src, "dst_id": dst, "type": type_, **extra}, headers=headers
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    ids = {
        "a": entity("person", "Ana Sintética"), "b": entity("email", "ana@ejemplo.test"),
        "c": entity("domain", "ejemplo.test"), "d": entity("ip", "203.0.113.7"),
        "solo": entity("person", "Persona Aislada"),
        "prop": entity("organization", "Org Propuesta", status="proposed", confidence=0.4),
    }
    rels = {
        "ab": relation(ids["a"], ids["b"], "uses"), "bc": relation(ids["b"], ids["c"], "at"),
        "cd": relation(ids["c"], ids["d"], "resolves_to"),
        "prop": relation(ids["a"], ids["prop"], "member_of", status="proposed", confidence=0.4),
    }
    return base, headers, ids, rels, entity, relation


def test_entity_validation_and_crud(client, graph):
    base, headers, ids, rels, *_ = graph
    bad_type = client.post(f"{base}/entities", json={"type": "spaceship", "label": "X"}, headers=headers)
    assert bad_type.status_code == 422 and "Tipo de entidad inválido" in bad_type.json()["detail"]
    assert client.post(f"{base}/entities", json={"type": "person", "label": "  "}, headers=headers).status_code == 422
    assert client.post(
        f"{base}/entities", json={"type": "person", "label": "X", "confidence": 1.5}, headers=headers
    ).status_code == 422
    assert client.post(
        f"{base}/entities", json={"type": "person", "label": "X", "status": "rejected"}, headers=headers
    ).status_code == 422
    assert client.post(
        f"{base}/relations", json={"src_id": ids["a"], "dst_id": ids["a"], "type": "loop"}, headers=headers
    ).status_code == 422
    assert client.post(
        f"{base}/relations", json={"src_id": ids["a"], "dst_id": 9999, "type": "x"}, headers=headers
    ).status_code == 404

    patched = client.patch(
        f"{base}/entities/{ids['a']}", json={"label": "Ana S.", "props": {"alias": "ana"}}, headers=headers
    ).json()
    assert patched["label"] == "Ana S." and patched["props"] == {"alias": "ana"}
    assert client.patch(f"{base}/entities/{ids['a']}", json={"type": "nave"}, headers=headers).status_code == 422
    rel = client.patch(f"{base}/relations/{rels['ab']}", json={"confidence": 0.5}, headers=headers).json()
    assert rel["confidence"] == 0.5 and rel["type"] == "uses"

    listing = client.get(f"{base}/entities", params={"type": ["person"], "q": "aislada"}, headers=headers).json()
    assert [e["id"] for e in listing["items"]] == [ids["solo"]]
    assert client.get(f"{base}/entities", params={"type": "nave"}, headers=headers).status_code == 422
    touching = client.get(f"{base}/relations", params={"entity_id": ids["b"]}, headers=headers).json()
    assert {r["id"] for r in touching["items"]} == {rels["ab"], rels["bc"]}

    # borrar una entidad se lleva sus relaciones
    assert client.delete(f"{base}/entities/{ids['d']}", headers=headers).status_code == 204
    assert client.get(f"{base}/relations/{rels['cd']}", headers=headers).status_code == 404
    assert client.delete(f"{base}/relations/{rels['bc']}", headers=headers).status_code == 204
    assert client.get(f"{base}/relations", headers=headers).json()["total"] == 2


def test_graph_view_and_filters(client, graph):
    base, headers, ids, rels, *_ = graph
    full = client.get(f"{base}/graph", headers=headers).json()
    assert {n["id"] for n in full["nodes"]} == set(ids.values())
    assert {e["id"] for e in full["edges"]} == set(rels.values())
    assert full["counts"] == {"nodes": 6, "edges": 4}
    node = next(n for n in full["nodes"] if n["id"] == ids["a"])
    assert node["degree"] == 2 and node["type"] == "person" and node["label"] == "Ana Sintética"
    edge = next(e for e in full["edges"] if e["id"] == rels["ab"])
    assert (edge["source"], edge["target"], edge["type"]) == (ids["a"], ids["b"], "uses")

    confirmed = client.get(f"{base}/graph", params={"status": "confirmed"}, headers=headers).json()
    assert ids["prop"] not in {n["id"] for n in confirmed["nodes"]}
    assert rels["prop"] not in {e["id"] for e in confirmed["edges"]}

    typed = client.get(f"{base}/graph", params={"type": ["email", "domain"]}, headers=headers).json()
    assert {n["id"] for n in typed["nodes"]} == {ids["b"], ids["c"]}
    assert [e["id"] for e in typed["edges"]] == [rels["bc"]]  # solo aristas con ambos extremos presentes

    connected = client.get(f"{base}/graph", params={"include_isolated": "false"}, headers=headers).json()
    assert ids["solo"] not in {n["id"] for n in connected["nodes"]}
    strong = client.get(f"{base}/graph", params={"min_confidence": 0.9}, headers=headers).json()
    assert ids["prop"] not in {n["id"] for n in strong["nodes"]}
    limited = client.get(f"{base}/graph", params={"limit": 2}, headers=headers).json()
    assert len(limited["nodes"]) == 2 and limited["truncated"] is True
    assert client.get(f"{base}/graph", params={"status": "inventado"}, headers=headers).status_code == 422


def test_search(client, graph, auth, synth):
    base, headers, ids, *_ = graph
    client.post(f"{base}/collections", json=synth.collection().model_dump(mode="json"), headers=headers)
    found = client.get(f"{base}/search", params={"q": "EJEMPLO.test"}, headers=headers).json()
    assert {e["id"] for e in found["entities"]} >= {ids["b"], ids["c"]}
    posts = client.get(f"{base}/search", params={"q": "sobre el puerto"}, headers=headers).json()
    assert posts["totals"]["posts"] == 6 and posts["posts"][0]["handle"] in ("alfa", "beta")
    accounts = client.get(f"{base}/search", params={"q": "sintética alfa"}, headers=headers).json()
    assert [a["handle"] for a in accounts["accounts"]] == ["alfa"]
    # los comodines de LIKE se buscan como texto
    assert client.get(f"{base}/search", params={"q": "%%"}, headers=headers).json()["totals"]["entities"] == 0
    assert client.get(f"{base}/search", params={"q": "a"}, headers=headers).status_code == 422


def test_neighbors_and_shortest_path(client, graph):
    base, headers, ids, rels, *_ = graph
    near = client.get(f"{base}/entities/{ids['b']}/neighbors", headers=headers).json()
    assert {n["id"] for n in near["nodes"]} == {ids["a"], ids["b"], ids["c"]}
    assert {e["id"] for e in near["edges"]} == {rels["ab"], rels["bc"]}
    far = client.get(f"{base}/entities/{ids['a']}/neighbors", params={"depth": 3}, headers=headers).json()
    assert {n["id"] for n in far["nodes"]} == {ids["a"], ids["b"], ids["c"], ids["d"], ids["prop"]}
    alone = client.get(f"{base}/entities/{ids['solo']}/neighbors", headers=headers).json()
    assert [n["id"] for n in alone["nodes"]] == [ids["solo"]] and alone["edges"] == []

    path = client.get(f"{base}/graph/path", params={"source": ids["d"], "target": ids["a"]}, headers=headers).json()
    assert path["found"] is True and path["length"] == 3
    assert [n["id"] for n in path["nodes"]] == [ids["d"], ids["c"], ids["b"], ids["a"]]
    assert {e["id"] for e in path["edges"]} == {rels["cd"], rels["bc"], rels["ab"]}
    none = client.get(f"{base}/graph/path", params={"source": ids["a"], "target": ids["solo"]}, headers=headers).json()
    assert none == {"found": False, "length": None, "nodes": [], "edges": []}
    # lo propuesto se puede excluir del camino
    only = client.get(
        f"{base}/graph/path", params={"source": ids["a"], "target": ids["prop"], "status": "confirmed"}, headers=headers
    ).json()
    assert only["found"] is False
    assert client.get(f"{base}/graph/path", params={"source": ids["a"], "target": 9999}, headers=headers).status_code == 404


def test_merge_repoints_relations(client, graph, db):
    base, headers, ids, rels, entity, relation = graph
    dup = entity("person", "Ana S. (duplicada)", props={"alias": "anita"})
    r_new = relation(dup, ids["d"], "seen_at")          # se reapunta a 'a'
    r_twin = relation(dup, ids["b"], "uses", confidence=0.3)  # ya existe a -uses-> b: se elimina
    r_loop = relation(ids["a"], dup, "same_as")         # quedaría a -> a: se elimina
    r_in = relation(ids["solo"], dup, "knows")          # entrante: se reapunta

    assert client.post(f"{base}/entities/merge", json={"keep_id": dup, "duplicate_id": dup},
                       headers=headers).status_code == 400
    assert client.post(f"{base}/entities/merge", json={"keep_id": ids["b"], "duplicate_id": dup},
                       headers=headers).status_code == 400  # tipos distintos

    merged = client.post(f"{base}/entities/merge", json={"keep_id": ids["a"], "duplicate_id": dup}, headers=headers)
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["relations_repointed"] == 2 and body["relations_removed"] == 2
    assert body["entity"]["id"] == ids["a"] and body["entity"]["props"]["alias"] == "anita"
    assert body["entity"]["props"]["merged_from"][0]["id"] == dup

    assert client.get(f"{base}/entities/{dup}", headers=headers).status_code == 404
    moved = client.get(f"{base}/relations/{r_new}", headers=headers).json()
    assert (moved["src_id"], moved["dst_id"]) == (ids["a"], ids["d"])
    incoming = client.get(f"{base}/relations/{r_in}", headers=headers).json()
    assert (incoming["src_id"], incoming["dst_id"]) == (ids["solo"], ids["a"])
    for gone in (r_twin, r_loop):
        assert client.get(f"{base}/relations/{gone}", headers=headers).status_code == 404
    assert client.get(f"{base}/relations/{rels['ab']}", headers=headers).json()["confidence"] == 1.0
    everything = client.get(f"{base}/relations", params={"limit": 500}, headers=headers).json()["items"]
    assert all(dup not in (r["src_id"], r["dst_id"]) for r in everything)
    assert audit.verify_chain(db) == (True, None)


def test_review_proposed_items(client, graph, auth):
    base, headers, ids, rels, entity, relation = graph
    assert client.post(f"{base}/entities/{ids['a']}/review", json={"decision": "accept"},
                       headers=headers).status_code == 409  # no está propuesta
    assert client.post(f"{base}/entities/{ids['prop']}/review", json={"decision": "quizás"},
                       headers=headers).status_code == 422
    assert client.post(f"{base}/entities/{ids['prop']}/review", json={"decision": "accept"},
                       headers=auth("auditor")).status_code == 403

    accepted = client.post(f"{base}/entities/{ids['prop']}/review", json={"decision": "accept", "note": "ok"},
                           headers=headers)
    assert accepted.status_code == 200 and accepted.json()["status"] == "confirmed"
    assert client.post(f"{base}/relations/{rels['prop']}/review", json={"decision": "accept"},
                       headers=headers).json()["status"] == "confirmed"

    # rechazar una entidad propuesta arrastra sus relaciones propuestas y la saca del grafo por defecto
    ghost = entity("person", "Fantasma", status="proposed")
    ghost_rel = relation(ids["a"], ghost, "knows", status="proposed")
    other_rel = relation(ids["b"], ids["c"], "guess", status="proposed")
    rejected = client.post(f"{base}/entities/{ghost}/review", json={"decision": "reject"}, headers=headers).json()
    assert rejected["status"] == "rejected"
    assert client.get(f"{base}/relations/{ghost_rel}", headers=headers).json()["status"] == "rejected"
    assert client.get(f"{base}/relations/{other_rel}", headers=headers).json()["status"] == "proposed"
    nodes = {n["id"] for n in client.get(f"{base}/graph", headers=headers).json()["nodes"]}
    assert ghost not in nodes
    with_rejected = client.get(f"{base}/graph", params={"status": ["rejected"]}, headers=headers).json()
    assert [n["id"] for n in with_rejected["nodes"]] == [ghost]


def test_sources_admiralty_and_upload(client, graph, auth, settings, tmp_path, db):
    base, headers, ids, *_ = graph
    ok = client.post(f"{base}/sources", headers=headers, json={
        "kind": "manual", "reference": "https://ejemplo.test/nota", "reliability": "b", "credibility": 2,
    })
    assert ok.status_code == 201, ok.text
    assert ok.json()["admiralty"] == "B2" and ok.json()["has_raw"] is False
    for bad in ({"reliability": "G"}, {"credibility": "7"}, {"credibility": "0"}, {"kind": "rumor"},
                {"sha256": "abc"}):
        response = client.post(f"{base}/sources", json={"reference": "x", **bad}, headers=headers)
        assert response.status_code == 422, bad
    assert "Admiralty" in client.post(f"{base}/sources", json={"reliability": "Z"}, headers=headers).json()["detail"]

    content = b"handle,texto\nalfa,hola\n" * 50
    sha = hashlib.sha256(content).hexdigest()
    upload = client.post(
        f"{base}/sources/upload", headers=headers, files={"file": ("../../exportación.csv", content, "text/csv")},
        data={"reliability": "A", "credibility": "1"},
    )
    assert upload.status_code == 201, upload.text
    source = upload.json()
    assert source["sha256"] == sha and source["kind"] == "upload" and source["has_raw"] is True
    assert source["reference"] == "exportación.csv" and source["admiralty"] == "A1"
    assert "raw_path" not in source
    stored = list((tmp_path / "data").rglob("*.csv"))
    assert [p.name for p in stored] == [f"{sha}.csv"] and stored[0].read_bytes() == content
    assert stored[0].parent == tmp_path / "data" / "cases" / base.rsplit("/", 1)[1] / "sources"

    bad_upload = client.post(f"{base}/sources/upload", headers=headers, files={"file": ("a.txt", b"x")},
                             data={"reliability": "Q"})
    assert bad_upload.status_code == 422
    assert client.post(f"{base}/sources/upload", headers=headers, files={"file": ("a.txt", b"")}).status_code == 422
    assert client.post(f"{base}/sources/upload", headers=auth("auditor"),
                       files={"file": ("a.txt", b"x")}).status_code == 403

    raw = client.get(f"{base}/sources/{source['id']}/raw", headers=auth("auditor"))
    assert raw.status_code == 200 and raw.content == content and raw.headers["x-aleph-sha256"] == sha
    assert client.get(f"{base}/sources/{ok.json()['id']}/raw", headers=headers).status_code == 404
    stored[0].write_bytes(content + b"alterado")
    tampered = client.get(f"{base}/sources/{source['id']}/raw", headers=headers)
    assert tampered.status_code == 409 and "alterada" in tampered.json()["detail"]

    used = client.post(f"{base}/entities", headers=headers,
                       json={"type": "document", "label": "exportación", "source_id": source["id"]})
    assert used.status_code == 201 and used.json()["source_id"] == source["id"]
    listing = client.get(f"{base}/sources", params={"kind": "upload"}, headers=headers).json()
    assert listing["total"] == 1
    assert audit.verify_chain(db) == (True, None)
