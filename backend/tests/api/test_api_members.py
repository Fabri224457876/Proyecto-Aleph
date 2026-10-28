"""Miembros de un caso: alta, baja, quién puede gestionarlos y auditoría."""

from sqlalchemy import select

from aleph.core import audit
from aleph.core.models import AuditEvent, CaseMember


def _base(case_id: int) -> str:
    return f"/api/cases/{case_id}/members"


def test_el_creador_queda_como_miembro(client, auth, make_case, users):
    case_id = make_case()
    listing = client.get(_base(case_id), headers=auth("analyst")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["user_id"] == users["analyst"]
    assert listing["items"][0]["username"] == "analyst" and listing["items"][0]["role"] == "analyst"
    assert listing["items"][0]["added_by"] == users["analyst"]


def test_analista_no_miembro_no_ve_ni_gestiona_los_miembros(client, auth, make_case, make_user):
    case_id = make_case()
    intruso = make_user("analista-ajeno", "analyst")
    assert client.get(_base(case_id), headers=intruso.headers).status_code == 404
    assert client.post(_base(case_id), json={"username": "auditor"}, headers=intruso.headers).status_code == 404
    assert client.delete(f"{_base(case_id)}/1", headers=intruso.headers).status_code == 404


def test_auditor_ve_los_miembros_pero_no_los_gestiona(client, auth, make_case, make_user, users):
    case_id = make_case()
    intruso = make_user("analista-ajeno", "analyst")
    assert client.get(_base(case_id), headers=auth("auditor")).status_code == 200
    assert client.post(_base(case_id), json={"user_id": intruso.id}, headers=auth("auditor")).status_code == 403
    assert client.delete(f"{_base(case_id)}/{users['analyst']}", headers=auth("auditor")).status_code == 403


def test_admin_agrega_analista_por_nombre_y_recien_ahi_ve_el_caso(client, auth, make_case, make_user, users):
    case_id = make_case()
    intruso = make_user("analista-ajeno", "analyst")
    assert client.get(f"/api/cases/{case_id}", headers=intruso.headers).status_code == 404

    added = client.post(_base(case_id), json={"username": "Analista-Ajeno"}, headers=auth("admin"))
    assert added.status_code == 201, added.text
    assert added.json()["user_id"] == intruso.id
    assert added.json()["added_by"] == users["admin"]
    assert client.get(f"/api/cases/{case_id}", headers=intruso.headers).status_code == 200


def test_miembro_agrega_analista_por_id_y_respeta_las_reglas_de_alta(client, auth, make_case, make_user, users):
    case_id = make_case()
    analyst = auth("analyst")
    intruso = make_user("analista-ajeno", "analyst")
    inactive = make_user("analista-inactivo", "analyst", active=False)

    assert client.post(_base(case_id), json={"user_id": intruso.id}, headers=analyst).status_code == 201
    again = client.post(_base(case_id), json={"user_id": intruso.id}, headers=analyst)
    assert again.status_code == 409 and "ya es miembro" in again.json()["detail"]

    # Admin y auditor ven todos los casos: no se agregan como miembros
    for username in ("auditor", "admin"):
        response = client.post(_base(case_id), json={"username": username}, headers=analyst)
        assert response.status_code == 409, username
    assert client.post(_base(case_id), json={"user_id": inactive.id}, headers=analyst).status_code == 409
    assert client.post(_base(case_id), json={"username": "nadie-existe"}, headers=analyst).status_code == 404
    assert client.post(_base(case_id), json={"user_id": 999999}, headers=analyst).status_code == 404


def test_alta_exige_exactamente_un_identificador(client, auth, make_case, users):
    case_id = make_case()
    analyst = auth("analyst")
    assert client.post(_base(case_id), json={}, headers=analyst).status_code == 422
    both = client.post(_base(case_id), json={"user_id": users["auditor"], "username": "auditor"}, headers=analyst)
    assert both.status_code == 422
    assert client.post(_base(case_id), json={"username": "   "}, headers=analyst).status_code == 422


def test_quitar_un_miembro_y_la_regla_del_ultimo_analista(client, auth, make_case, make_user, users):
    case_id = make_case()
    analyst, admin = auth("analyst"), auth("admin")
    intruso = make_user("analista-ajeno", "analyst")
    client.post(_base(case_id), json={"user_id": intruso.id}, headers=analyst)

    # Un miembro puede quitar a otro analista mientras quede alguno
    assert client.delete(f"{_base(case_id)}/{intruso.id}", headers=analyst).status_code == 204
    assert client.get(f"/api/cases/{case_id}", headers=intruso.headers).status_code == 404
    assert client.delete(f"{_base(case_id)}/{intruso.id}", headers=analyst).status_code == 404  # ya no es miembro

    # Único analista miembro: no puede quitarse a sí mismo
    blocked = client.delete(f"{_base(case_id)}/{users['analyst']}", headers=analyst)
    assert blocked.status_code == 409
    assert client.get(f"/api/cases/{case_id}", headers=analyst).status_code == 200

    # Un admin sí puede dejar el caso sin analistas
    assert client.delete(f"{_base(case_id)}/{users['analyst']}", headers=admin).status_code == 204
    assert client.get(f"/api/cases/{case_id}", headers=analyst).status_code == 404
    assert client.get(f"/api/cases/{case_id}", headers=admin).status_code == 200


def test_gestion_de_miembros_queda_auditada_en_la_cadena(client, auth, make_case, make_user, users, session_factory):
    case_id = make_case()
    analyst = auth("analyst")
    intruso = make_user("analista-ajeno", "analyst")
    client.post(_base(case_id), json={"user_id": intruso.id}, headers=analyst)
    client.delete(f"{_base(case_id)}/{intruso.id}", headers=analyst)
    client.get(_base(case_id), headers=analyst)

    with session_factory() as session:
        events = session.execute(
            select(AuditEvent).where(AuditEvent.case_id == case_id, AuditEvent.action.in_(
                ["case.member.add", "case.member.remove", "case.members.view"]
            )).order_by(AuditEvent.id)
        ).scalars().all()
        assert [e.action for e in events] == ["case.member.add", "case.member.remove", "case.members.view"]
        added, removed = events[0], events[1]
        assert added.user_id == users["analyst"] and added.target == f"user:{intruso.id}"
        assert added.detail == {"user_id": intruso.id, "username": "analista-ajeno", "role": "analyst"}
        assert removed.target == f"user:{intruso.id}"
        assert audit.verify_chain(session) == (True, None)


def test_el_alta_del_creador_queda_auditada_con_el_caso(client, auth, make_case, users, session_factory):
    case_id = make_case()
    with session_factory() as session:
        created = session.execute(
            select(AuditEvent).where(AuditEvent.case_id == case_id, AuditEvent.action == "case.create")
        ).scalars().one()
        assert created.detail["member_ids"] == [users["analyst"]]


def test_borrar_un_caso_vacio_se_lleva_sus_miembros(client, auth, make_case, session_factory):
    case_id = make_case()  # el analista queda como miembro, y el caso no tiene más datos
    assert client.delete(f"/api/cases/{case_id}", headers=auth("admin")).status_code == 204
    with session_factory() as session:
        leftovers = session.execute(select(CaseMember).where(CaseMember.case_id == case_id)).scalars().all()
        assert leftovers == []
