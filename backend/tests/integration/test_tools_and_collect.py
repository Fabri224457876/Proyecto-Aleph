"""Herramientas OSINT y recolección por conector desde la API. Sin red: los conectores usan MockTransport."""

import io
import json
from pathlib import Path

import httpx
from PIL import Image

from aleph.api.services.outbound import outbound_http
from aleph.main import app

WALLET_ETH = "0xd8c2f1a4e9b7c3d5a6f0e1b2c3d4e5f607182930"
CSV_BYTES = (
    b"platform,handle,text,created_at,post_id\n"
    b"bluesky,alfa,hola mundo,2026-03-01T10:00:00Z,p1\n"
    b"bluesky,alfa,otra publicacion,2026-03-01T11:00:00Z,p2\n"
)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, format="PNG")
    return buf.getvalue()


def bluesky_handler(profile_status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("app.bsky.actor.getProfile"):
            if profile_status != 200:
                return httpx.Response(400, json={"error": "Profile not found"})
            return httpx.Response(200, json={
                "handle": "ejemplo.test", "did": "did:plc:ejemplo", "displayName": "Cuenta de prueba",
                "description": "Biografía ficticia", "createdAt": "2024-01-01T00:00:00Z",
                "followersCount": 3, "followsCount": 2,
            })
        if path.endswith("app.bsky.feed.getAuthorFeed"):
            return httpx.Response(200, json={"feed": [{"post": {
                "uri": "at://did:plc:ejemplo/app.bsky.feed.post/3k1",
                "author": {"handle": "ejemplo.test"},
                "record": {"text": "Hola #demo https://ejemplo.example/nota", "createdAt": "2026-03-01T10:00:00Z"},
                "indexedAt": "2026-03-01T10:00:05Z",
            }}], "cursor": None})
        if path.endswith("app.bsky.graph.getFollows"):
            return httpx.Response(200, json={"follows": [], "cursor": None})
        if path.endswith("app.bsky.graph.getFollowers"):
            return httpx.Response(200, json={"followers": [], "cursor": None})
        return httpx.Response(404, json={})

    return handler


# ---------------------------------------------------------------- herramientas OSINT


def test_catalogo_de_herramientas(client, token):
    tools = client.get("/api/tools", headers=token("analista-demo")).json()
    names = {t["name"] for t in tools}
    assert len(tools) == 13 and {"dorks", "phone_intel", "image_meta", "wallet", "evidence"} <= names
    assert client.get("/api/tools").status_code == 401


def test_wallet_sin_red_guarda_la_entidad_como_propuesta(client, token, fresh_case):
    headers = token("analista-demo")
    run = client.post(f"/api/cases/{fresh_case}/tools/wallet/run", headers=headers, data={"target": WALLET_ETH})
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["tool"] == "wallet" and body["persisted"]["entities_created"] == 1
    entity = client.get(f"/api/cases/{fresh_case}/entities", headers=headers).json()["items"][0]
    assert (entity["type"], entity["status"]) == ("wallet", "proposed")
    source = client.get(f"/api/cases/{fresh_case}/sources/{body['source_id']}", headers=headers).json()
    assert source["kind"] == "manual" and source["connector"] == "osint:wallet" and source["has_raw"] is True


def test_herramientas_con_archivo_y_sin_entidades(client, token, fresh_case):
    headers = token("analista-demo")
    base = f"/api/cases/{fresh_case}/tools"
    image = client.post(f"{base}/image_meta/run", headers=headers,
                        files={"file": ("foto.png", _png(), "image/png")})
    assert image.status_code == 201, image.text
    assert image.json()["persisted"]["entities_created"] == 2  # el archivo y su hash SHA-256
    dorks = client.post(f"{base}/dorks/run", headers=headers, data={
        "target": "ejemplo.example", "params": json.dumps({"target_type": "domain", "evil": "x"}),
    })
    assert dorks.status_code == 201, dorks.text
    assert dorks.json()["persisted"]["entity_ids"] == [] and dorks.json()["ignored_params"] == ["evil"]
    phone = client.post(f"{base}/phone_intel/run", headers=headers, data={"target": "+54 11 5555-0101"})
    assert phone.status_code == 201 and phone.json()["persisted"]["entities_created"] >= 2


def test_errores_de_herramientas(client, token, fresh_case):
    headers = token("analista-demo")
    base = f"/api/cases/{fresh_case}/tools"
    assert client.post(f"{base}/no_existe/run", headers=headers, data={"target": "x"}).status_code == 404
    assert client.post(f"{base}/dorks/run", headers=headers, data={}).status_code == 400
    assert client.post(f"{base}/image_meta/run", headers=headers, data={"target": "x"}).status_code == 400
    rejected = client.post(f"{base}/dorks/run", headers=headers, data={"target": "x.example"},
                           files={"file": ("a.txt", b"hola", "text/plain")})
    assert rejected.status_code == 400 and "no recibe archivos" in rejected.json()["detail"]
    assert client.post(f"{base}/dorks/run", headers=headers, data={"target": "x", "params": "{mal"}
                       ).status_code == 400
    assert client.post(f"{base}/dorks/run", headers=token("auditor-demo"), data={"target": "x.example"}
                       ).status_code == 403


# ---------------------------------------------------------------- recolección


def test_conector_en_vivo_solo_recibe_parametros_declarados(client, token, fresh_case, settings):
    app.dependency_overrides[outbound_http] = lambda: _client(bluesky_handler())
    headers = token("analista-demo")
    body = {"connector": "bluesky",
            "params": json.dumps({"handle": "ejemplo.test", "limit": 5, "path": "/etc/passwd"})}
    run = client.post(f"/api/cases/{fresh_case}/collect", headers=headers, data=body)
    assert run.status_code == 201, run.text
    out = run.json()
    assert out["connector"] == "bluesky" and out["ignored_params"] == ["path"]
    assert out["ingest"]["accounts_created"] == 1 and out["ingest"]["posts_created"] == 1
    accounts = client.get(f"/api/cases/{fresh_case}/accounts", headers=headers).json()
    assert accounts["items"][0]["handle"] == "ejemplo.test" and accounts["items"][0]["platform"] == "bluesky"
    jobs = client.get("/api/jobs", params={"case_id": fresh_case, "kind": "collect"}, headers=headers).json()
    assert jobs["total"] == 1 and jobs["items"][0]["status"] == "done"


def test_importacion_con_archivo_subido(client, token, fresh_case, settings):
    headers = token("analista-demo")
    data = {"connector": "generic_csv",
            "params": json.dumps({"path": "C:/Windows/win.ini", "platform": "bluesky", "limit": 10})}
    first = client.post(f"/api/cases/{fresh_case}/collect", headers=headers, data=data,
                        files={"file": ("ejemplo.csv", CSV_BYTES, "text/csv")})
    assert first.status_code == 201, first.text
    out = first.json()
    assert out["ignored_params"] == ["path"]  # la ruta nunca viene del cliente
    assert out["ingest"]["accounts_created"] == 1 and out["ingest"]["posts_created"] == 2
    stored = list(Path(settings.data_dir, "cases", str(fresh_case), "uploads").glob("*/ejemplo.csv"))
    assert len(stored) == 1
    again = client.post(f"/api/cases/{fresh_case}/collect", headers=headers, data=data,
                        files={"file": ("ejemplo.csv", CSV_BYTES, "text/csv")})
    assert again.status_code == 201 and again.json()["ingest"]["source_created"] is False
    assert again.json()["ingest"]["posts_created"] == 0


def test_errores_de_recoleccion(client, token, fresh_case):
    headers = token("analista-demo")
    url = f"/api/cases/{fresh_case}/collect"
    assert client.post(url, headers=headers, data={"connector": "no_existe"}).status_code == 404
    missing = client.post(url, headers=headers, data={"connector": "bluesky"})
    assert missing.status_code == 400 and "handle" in missing.json()["detail"]
    no_file = client.post(url, headers=headers, data={"connector": "generic_csv", "params": "{}"})
    assert no_file.status_code == 400 and "archivo" in no_file.json()["detail"]
    with_file = client.post(url, headers=headers, data={"connector": "bluesky", "params": '{"handle": "x"}'},
                            files={"file": ("a.csv", b"x", "text/csv")})
    assert with_file.status_code == 400 and "no recibe archivos" in with_file.json()["detail"]
    no_key = client.post(url, headers=headers, data={"connector": "x_api", "params": '{"handle": "x"}'})
    assert no_key.status_code == 503 and "ALEPH_X_BEARER_TOKEN" in no_key.json()["detail"]
    assert client.post(url, headers=headers, data={"connector": "bluesky", "params": "no-es-json"}
                       ).status_code == 400
    assert client.post(url, headers=token("auditor-demo"), data={"connector": "bluesky"}).status_code == 403


def test_fallo_del_conector_queda_registrado(client, token, fresh_case):
    app.dependency_overrides[outbound_http] = lambda: _client(bluesky_handler(profile_status=400))
    headers = token("analista-demo")
    failed = client.post(f"/api/cases/{fresh_case}/collect", headers=headers,
                         data={"connector": "bluesky", "params": '{"handle": "no-existe.test"}'})
    assert failed.status_code == 502 and "no encontrada" in failed.json()["detail"]
    jobs = client.get("/api/jobs", params={"case_id": fresh_case, "status": "failed"}, headers=headers).json()
    assert jobs["total"] == 1 and "no encontrada" in jobs["items"][0]["error"]
    assert client.get(f"/api/cases/{fresh_case}/accounts", headers=headers).json()["total"] == 0


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
