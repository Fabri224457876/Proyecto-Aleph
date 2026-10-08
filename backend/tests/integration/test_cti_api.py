"""CTI desde la API: IOCs, enriquecimiento, exportación e importación STIX/MISP, ATT&CK y datachunks.

Sin red: los proveedores de enriquecimiento reciben un MockTransport, y el resto no sale del proceso.
"""

import httpx

from aleph.api.services.outbound import enrichment_providers
from aleph.cti.enrich import CrtSh
from aleph.main import app

from .conftest import mock_crt_sh_rows


def test_iocs_de_un_texto_como_propuestas(client, token, fresh_case):
    headers = token("analista-demo")
    text = ("Muestra: https://pagos-seguros.example/login y la IP 203.0.113.7. Hash "
            "3f79bb7b435b05321651daefd374cdc681dc06faa65e374e38337b88ca046dea. Dominio pagos[.]example.")
    out = client.post(f"/api/cases/{fresh_case}/ioc/extract", headers=headers, json={"text": text})
    assert out.status_code == 201, out.text
    body = out.json()
    assert body["defanged_input"] is True and body["persisted"]["entities_created"] >= 3
    assert any("TLD no reconocido" in d["reason"] for d in body["discarded"])  # .example no está en IANA
    entities = client.get(f"/api/cases/{fresh_case}/entities", headers=headers).json()["items"]
    assert entities and all(e["status"] == "proposed" for e in entities)
    assert {e["type"] for e in entities} >= {"url", "ip", "hash"}
    assert client.post(f"/api/cases/{fresh_case}/ioc/extract", headers=headers, json={"text": ""}
                       ).status_code == 422


def test_enriquecimiento_con_proveedor_simulado(client, token, fresh_case, mock_http):
    headers = token("analista-demo")
    base = f"/api/cases/{fresh_case}"
    domain = client.post(f"{base}/entities", headers=headers,
                         json={"type": "domain", "label": "ejemplo.example"}).json()

    def crt_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "crt.sh"
        return httpx.Response(200, json=mock_crt_sh_rows())

    app.dependency_overrides[enrichment_providers] = lambda: [CrtSh(mock_http(crt_handler))]
    out = client.post(f"{base}/entities/{domain['id']}/enrich", headers=headers)
    assert out.status_code == 201, out.text
    body = out.json()
    assert body["results"][0]["provider"] == "crtsh" and body["results"][0]["status"] == "ok"
    assert body["persisted"]["entities_created"] == 2 and body["persisted"]["relations_created"] == 2
    subs = client.get(f"{base}/entities", params={"q": ".ejemplo.example", "status": "proposed"},
                      headers=headers).json()
    assert {e["label"] for e in subs["items"]} == {"www.ejemplo.example", "mail.ejemplo.example"}
    person = client.post(f"{base}/entities", headers=headers,
                         json={"type": "person", "label": "Persona de prueba"}).json()
    assert client.post(f"{base}/entities/{person['id']}/enrich", headers=headers).status_code == 400
    sources = client.get(f"{base}/sources", params={"kind": "enrichment"}, headers=headers).json()
    assert sources["total"] == 1 and sources["items"][0]["has_raw"] is True


def test_stix_default_solo_confirmado_y_tlp(client, token, demo_case):
    headers = token("analista-demo")
    base = f"/api/cases/{demo_case}"
    stix = client.get(f"{base}/export/stix", headers=headers)
    assert stix.status_code == 200 and stix.headers["content-disposition"].endswith('.stix.json"')
    assert len(stix.headers["x-aleph-sha256"]) == 64
    # Todo el caso es amber: con TLP máximo clear no queda nada que exportar
    nothing = client.get(f"{base}/export/stix", params={"max_tlp": "clear"}, headers=headers)
    assert nothing.status_code == 409 and "No hay objetos exportables" in nothing.json()["detail"]
    misp = client.get(f"{base}/export/misp", headers=headers)
    assert misp.status_code == 200
    event = misp.json()["Event"]
    assert len(event["Attribute"]) > 10 and any(t["name"] == "tlp:amber" for t in event["Tag"])
    hidden = client.get(f"{base}/export/misp", params={"max_tlp": "clear"}, headers=headers).json()["Event"]
    assert hidden["Attribute"] == [] and hidden["info"].startswith("Caso con metadatos ocultos")
    assert client.get(f"{base}/export/stix", headers=token("auditor-demo")).status_code == 200


def test_stix_import_queda_como_propuesta_y_valida_el_bundle(client, token, demo_case, fresh_case):
    headers = token("analista-demo")
    bundle = client.get(f"/api/cases/{demo_case}/export/stix", headers=headers).json()
    imported = client.post(f"/api/cases/{fresh_case}/import/stix", headers=headers, json=bundle)
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["entidades_creadas"] >= 20 and body["tecnicas_propuestas"] == 3
    assert body["caso_del_informe"]["nombre"].startswith("[FICTICIO]")
    confirmed = client.get(f"/api/cases/{fresh_case}/entities", params={"status": "confirmed"},
                           headers=headers).json()
    assert confirmed["total"] == 0  # nada entra confirmado
    assert client.get(f"/api/cases/{fresh_case}/export/stix", headers=headers).status_code == 409
    assert client.get(f"/api/cases/{fresh_case}/export/stix", params={"include_pending": "true"},
                      headers=headers).status_code == 200
    assert client.post(f"/api/cases/{fresh_case}/import/stix", headers=headers, json={"type": "report"}
                       ).status_code == 400
    broken = {"type": "bundle", "id": "bundle--00000000-0000-4000-8000-000000000000",
              "objects": [{"type": "malware"}]}
    rejected = client.post(f"/api/cases/{fresh_case}/import/stix", headers=headers, json=broken)
    assert rejected.status_code == 400 and "no es un bundle STIX" in rejected.json()["detail"]


def test_attack_busqueda_vinculos_y_capa(client, token, fresh_case):
    headers = token("analista-demo")
    found = client.get("/api/attack/techniques", params={"q": "phishing"}, headers=headers).json()
    assert "T1566" in {t["id"] for t in found} and all("url" in t for t in found)
    subs = client.get("/api/attack/techniques", params={"q": "T1566"}, headers=headers).json()
    assert {"T1566", "T1566.001", "T1566.002"} <= {t["id"] for t in subs}  # búsqueda por ID y subtécnicas
    assert client.get("/api/attack/techniques", params={"q": ""}, headers=headers).status_code == 422
    base = f"/api/cases/{fresh_case}"
    first = client.post(f"{base}/attack/techniques", headers=headers,
                        json={"technique_id": "t1566.001", "score": 50, "comment": "primera lectura"})
    assert first.status_code == 201 and first.json()["technique_id"] == "T1566.001" and first.json()["created"]
    again = client.post(f"{base}/attack/techniques", headers=headers,
                        json={"technique_id": "T1566.001", "score": 60, "comment": "revisada"})
    assert again.status_code == 201 and again.json()["created"] is False
    assert client.post(f"{base}/attack/techniques", headers=headers, json={"technique_id": "T9999"}
                       ).status_code == 400
    layer = client.get(f"{base}/attack/layer", headers=headers).json()
    assert [(t["techniqueID"], t["score"]) for t in layer["techniques"]] == [("T1566.001", 60)]
    assert layer["versions"]["attack"] and layer["name"]


def test_datachunks_sin_llm_y_sin_guardar(client, token, fresh_case):
    headers = token("analista-demo")
    base = f"/api/cases/{fresh_case}"
    before = client.get(f"{base}/findings", headers=headers).json()["total"]
    text = ("Escribime a juan.perez@ejemplo.com.ar, DNI 12.345.678, mirá https://ejemplo.example/nota "
            "y el dominio pagos-seguros[.]example. Patente AB123CD. Hash "
            "3f79bb7b435b05321651daefd374cdc681dc06faa65e374e38337b88ca046dea.")
    out = client.post(f"{base}/captures/chunks", headers=headers, json={
        "text": text, "page_url": "https://foro.example/hilo/9", "platform": "X",
    })
    assert out.status_code == 200, out.text
    chunks = out.json()
    kinds = {c["kind"] for c in chunks}
    assert {"email", "document", "url", "hash", "vehicle"} <= kinds
    assert all(c["page_url"] == "https://foro.example/hilo/9" and c["platform"] == "x" for c in chunks)
    assert all(c["detected_by"] in ("rule", "funes") for c in chunks)
    assert not any("pagos-seguros" in c["value"] for c in chunks)  # .example queda fuera del extractor
    assert client.get(f"{base}/findings", headers=headers).json()["total"] == before


