"""Acceso por caso (need-to-know): todas las rutas del caso, el listado y los trabajos.

El barrido toma las rutas de `app.routes`: una ruta agregada en el futuro queda cubierta sin tocar
este archivo. Si no aparece en el barrido, no hay forma de que su acceso quede sin probar.
"""

import re

from fastapi.routing import APIRoute

from aleph.main import app

NOT_FOUND_CASE = "El caso {case_id} no existe."
# Ids de relleno para parámetros que no son el caso. Ningún registro real tiene ese id.
PLACEHOLDER_ID = 999999


def _api_routes() -> list[tuple[str, set[str]]]:
    """(ruta completa, métodos) de todas las rutas de la app, con los routers incluidos aplanados.

    FastAPI 0.142 guarda cada router incluido como `_IncludedRouter` y `effective_route_contexts()`
    da sus rutas con el prefijo completo. Con una versión que las guarde planas, se leen directo.
    """
    found: list[tuple[str, set[str]]] = []
    for route in app.routes:
        if hasattr(route, "effective_route_contexts"):
            for context in route.effective_route_contexts():
                if isinstance(context.original_route, APIRoute):
                    found.append((context.path, context.original_route.methods))
        elif isinstance(route, APIRoute):
            found.append((route.path, route.methods))
    return found


CASE_ROUTES = sorted({
    (method, path)
    for path, methods in _api_routes()
    if path.startswith("/api/cases/{case_id}")
    for method in methods
})


def _fill(path: str, case_id: int) -> str:
    return re.sub(
        r"\{(\w+)\}",
        lambda match: str(case_id) if match.group(1) == "case_id" else str(PLACEHOLDER_ID),
        path,
    )


def _hides_case(response, case_id: int) -> bool:
    """La respuesta es el 404 que da un caso inexistente, con el mismo texto."""
    return response.status_code == 404 and response.json().get("detail") == NOT_FOUND_CASE.format(case_id=case_id)


def test_el_barrido_cubre_las_rutas_del_caso():
    # Guarda contra un barrido vacío o incompleto: si cambia el registro de rutas, el test no puede pasar en falso
    assert len(CASE_ROUTES) >= 60, CASE_ROUTES
    for expected in [
        ("GET", "/api/cases/{case_id}"), ("PATCH", "/api/cases/{case_id}"), ("DELETE", "/api/cases/{case_id}"),
        ("GET", "/api/cases/{case_id}/members"), ("POST", "/api/cases/{case_id}/members"),
        ("DELETE", "/api/cases/{case_id}/members/{user_id}"), ("GET", "/api/cases/{case_id}/graph"),
        ("POST", "/api/cases/{case_id}/tools/{name}/run"), ("GET", "/api/cases/{case_id}/export/stix"),
    ]:
        assert expected in CASE_ROUTES, expected


def test_todas_las_rutas_del_caso_ocultan_el_caso_a_un_analista_no_miembro(client, auth, make_case, make_user):
    case_id = make_case("Caso del analista de prueba")
    intruso = make_user("analista-ajeno", "analyst")  # no es miembro: el caso lo abrió otro analista
    problems = []
    for method, path in CASE_ROUTES:
        response = client.request(method, _fill(path, case_id), headers=intruso.headers)
        if not _hides_case(response, case_id):
            problems.append(f"{method} {path}: {response.status_code} {response.text[:160]}")
    assert problems == [], "Rutas que no ocultan el caso a un no miembro:\n" + "\n".join(problems)


def test_todas_las_rutas_del_caso_dejan_pasar_a_un_miembro(client, auth, make_case):
    problems = []
    for method, path in CASE_ROUTES:
        # Caso nuevo por ruta: las que cambian estado (cerrar, borrar...) no afectan a las demás
        case_id = make_case("Caso propio")
        response = client.request(method, _fill(path, case_id), headers=auth("analyst"))
        if _hides_case(response, case_id):
            problems.append(f"{method} {path}: el miembro recibe el 404 del caso")
    assert problems == [], "Rutas que ocultan el caso a su propio miembro:\n" + "\n".join(problems)


def test_admin_y_auditor_ven_cualquier_caso(client, auth, make_case):
    case_id = make_case("Caso del analista")
    assert client.get(f"/api/cases/{case_id}", headers=auth("admin")).status_code == 200
    assert client.get(f"/api/cases/{case_id}", headers=auth("auditor")).status_code == 200
    assert client.get(f"/api/cases/{case_id}/graph", headers=auth("auditor")).status_code == 200


def test_listado_y_conteo_solo_muestran_los_casos_del_analista(client, auth, make_case):
    propio = make_case("Caso propio")
    ajeno = make_case("Caso que abrió el admin", role="admin")  # el admin no le da acceso a nadie
    listing = client.get("/api/cases", headers=auth("analyst")).json()
    assert listing["total"] == 1 and [c["id"] for c in listing["items"]] == [propio]
    assert client.get("/api/cases", params={"status": "open"}, headers=auth("analyst")).json()["total"] == 1
    assert client.get("/api/cases", params={"q": "admin"}, headers=auth("analyst")).json()["total"] == 0
    assert client.get(f"/api/cases/{ajeno}", headers=auth("analyst")).status_code == 404

    assert client.get("/api/cases", headers=auth("auditor")).json()["total"] == 2
    assert client.get("/api/cases", headers=auth("admin")).json()["total"] == 2


def test_el_caso_creado_por_el_admin_no_aparece_a_los_analistas_hasta_agregarlos(
    client, auth, make_case, make_user
):
    ajeno = make_case("Caso del admin", role="admin")
    intruso = make_user("analista-ajeno", "analyst")
    assert client.get("/api/cases", headers=intruso.headers).json()["total"] == 0
    added = client.post(f"/api/cases/{ajeno}/members", json={"user_id": intruso.id}, headers=auth("admin"))
    assert added.status_code == 201, added.text
    assert [c["id"] for c in client.get("/api/cases", headers=intruso.headers).json()["items"]] == [ajeno]


def test_jobs_solo_de_los_casos_del_analista(client, auth, make_case, make_user, synth, menard_engine):
    analyst = auth("analyst")
    propio = make_case("Caso propio")
    intruso = make_user("analista-ajeno", "analyst")
    ajeno = client.post("/api/cases", json={"name": "Caso ajeno", "legal_basis": synth.legal},
                        headers=intruso.headers).json()["id"]

    def run_menard(case_id: int, headers: dict) -> int:
        collected = client.post(f"/api/cases/{case_id}/collections", json=synth.collection().model_dump(mode="json"),
                                headers=headers)
        assert collected.status_code == 201, collected.text
        run = client.post(f"/api/cases/{case_id}/menard/run", headers=headers)
        assert run.status_code == 200, run.text
        return run.json()["job_id"]

    job_propio = run_menard(propio, analyst)
    job_ajeno = run_menard(ajeno, intruso.headers)

    mine = client.get("/api/jobs", headers=analyst).json()
    assert mine["total"] == 1 and [j["id"] for j in mine["items"]] == [job_propio]
    assert client.get(f"/api/jobs/{job_propio}", headers=analyst).status_code == 200
    # Un trabajo ajeno responde igual que uno inexistente
    hidden = client.get(f"/api/jobs/{job_ajeno}", headers=analyst)
    assert hidden.status_code == 404 and hidden.json()["detail"] == f"El trabajo {job_ajeno} no existe."
    assert client.get("/api/jobs", params={"case_id": ajeno}, headers=analyst).json()["total"] == 0

    everything = client.get("/api/jobs", headers=auth("auditor")).json()
    assert {job_propio, job_ajeno} <= {j["id"] for j in everything["items"]}
    assert client.get(f"/api/jobs/{job_ajeno}", headers=auth("admin")).status_code == 200


def test_quitar_al_analista_le_quita_tambien_sus_trabajos(client, auth, make_case, users, synth, menard_engine):
    case_id = make_case("Caso con trabajo")
    analyst = auth("analyst")
    client.post(f"/api/cases/{case_id}/collections", json=synth.collection().model_dump(mode="json"), headers=analyst)
    job_id = client.post(f"/api/cases/{case_id}/menard/run", headers=analyst).json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}", headers=analyst).status_code == 200

    removed = client.delete(f"/api/cases/{case_id}/members/{users['analyst']}", headers=auth("admin"))
    assert removed.status_code == 204, removed.text
    assert client.get(f"/api/cases/{case_id}", headers=analyst).status_code == 404
    assert client.get(f"/api/jobs/{job_id}", headers=analyst).status_code == 404
    assert client.get("/api/jobs", headers=analyst).json()["total"] == 0
