"""Acceso por caso sobre el caso de demostración que siembra el sembrador real."""

from aleph.demo import story

NEWCOMER = "analista-nuevo"
NEWCOMER_PASSWORD = "clave-larga-de-prueba-1"


def test_el_analista_demo_es_miembro_del_caso_demo(client, token, demo_case):
    members = client.get(f"/api/cases/{demo_case}/members", headers=token(story.DEMO_ANALYST)).json()
    assert story.DEMO_ANALYST in {m["username"] for m in members["items"]}
    # El auditor de demo ve todos los casos sin ser miembro
    assert client.get(f"/api/cases/{demo_case}", headers=token(story.DEMO_AUDITOR)).status_code == 200


def test_un_analista_nuevo_no_ve_el_caso_demo_hasta_que_el_admin_lo_agrega(client, token, demo_case):
    admin = token(story.DEMO_ADMIN)
    created = client.post("/api/users", json={"username": NEWCOMER, "password": NEWCOMER_PASSWORD,
                                              "role": "analyst"}, headers=admin)
    assert created.status_code == 201, created.text
    newcomer = token(NEWCOMER)

    assert client.get(f"/api/cases/{demo_case}", headers=newcomer).status_code == 404
    assert client.get("/api/cases", headers=newcomer).json()["total"] == 0

    added = client.post(f"/api/cases/{demo_case}/members", json={"username": NEWCOMER}, headers=admin)
    assert added.status_code == 201, added.text
    assert client.get(f"/api/cases/{demo_case}", headers=newcomer).status_code == 200
    assert [c["id"] for c in client.get("/api/cases", headers=newcomer).json()["items"]] == [demo_case]
