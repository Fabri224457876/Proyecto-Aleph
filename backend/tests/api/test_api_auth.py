import inspect

from sqlalchemy import select

from aleph.api import bootstrap, security
from aleph.core import audit
from aleph.core.models import AuditEvent, User


def _actions(db):
    return [e.action for e in db.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars()]


def test_login_ok_and_failed_are_audited(client, users, db, synth):
    ok = client.post("/api/auth/login", json={"username": "Analyst", "password": synth.password})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["token_type"] == "bearer" and body["user"]["role"] == "analyst"
    assert "password_hash" not in body["user"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["username"] == "analyst"

    bad = client.post("/api/auth/login", json={"username": "analyst", "password": "otra-clave-larga"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Usuario o contraseña incorrectos."
    ghost = client.post("/api/auth/login", json={"username": "nadie", "password": "otra-clave-larga"})
    assert ghost.status_code == 401 and ghost.json() == bad.json()

    assert _actions(db) == ["auth.login", "auth.login.failed", "auth.login.failed"]
    assert audit.verify_chain(db) == (True, None)


def test_token_required_and_validated(client, users, auth):
    assert client.get("/api/cases").status_code == 401
    assert client.get("/api/cases", headers={"Authorization": "Bearer no-es-un-token"}).status_code == 401
    assert client.get("/api/cases", headers=auth("auditor")).status_code == 200


def test_inactive_user_is_rejected(client, users, auth, db, synth):
    headers = auth("analyst")
    patched = client.patch(f"/api/users/{users['analyst']}", json={"active": False}, headers=auth("admin"))
    assert patched.status_code == 200 and patched.json()["active"] is False
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    login = client.post("/api/auth/login", json={"username": "analyst", "password": synth.password})
    assert login.status_code == 401


def test_role_permissions(client, auth, make_case, db):
    case_id = make_case()
    analyst, auditor, admin = auth("analyst"), auth("auditor"), auth("admin")
    new_case = {"name": "Otro", "legal_basis": "Base legal de prueba"}
    entity = {"type": "person", "label": "Persona Sintética"}

    # auditor: lee casos y auditoría, no escribe
    assert client.get(f"/api/cases/{case_id}", headers=auditor).status_code == 200
    assert client.get(f"/api/cases/{case_id}/graph", headers=auditor).status_code == 200
    assert client.post("/api/cases", json=new_case, headers=auditor).status_code == 403
    assert client.post(f"/api/cases/{case_id}/entities", json=entity, headers=auditor).status_code == 403
    assert client.post(f"/api/cases/{case_id}/close", headers=auditor).status_code == 403
    assert client.post(f"/api/cases/{case_id}/menard/run", headers=auditor).status_code == 403
    assert client.get("/api/audit/events", headers=auditor).status_code == 200
    assert client.get("/api/audit/verify", headers=auditor).status_code == 200

    # analista: trabaja casos, no ve la auditoría ni gestiona usuarios
    assert client.post(f"/api/cases/{case_id}/entities", json=entity, headers=analyst).status_code == 201
    denied = client.get("/api/audit/events", headers=analyst)
    assert denied.status_code == 403 and denied.json()["detail"] == "Tu rol no permite esta acción."
    assert client.get("/api/audit/verify", headers=analyst).status_code == 403
    assert client.get("/api/users", headers=analyst).status_code == 403
    user = {"username": "nuevo", "password": "una-clave-larga-1", "role": "analyst"}
    assert client.post("/api/users", json=user, headers=analyst).status_code == 403
    assert client.post("/api/users", json=user, headers=auditor).status_code == 403

    # admin: todo
    assert client.post("/api/users", json=user, headers=admin).status_code == 201
    assert client.post("/api/users", json=user, headers=admin).status_code == 409
    assert client.get("/api/users", headers=admin).json()["total"] == 4
    assert client.get("/api/audit/events", headers=admin).status_code == 200

    actions = _actions(db)
    assert actions.count("auth.denied") == 9
    assert audit.verify_chain(db) == (True, None)


def test_user_management_rules(client, auth, users):
    admin = auth("admin")
    weak = client.post("/api/users", json={"username": "x1y", "password": "corta"}, headers=admin)
    assert weak.status_code == 422
    assert "al menos" in weak.json()["detail"]
    bad_role = client.post(
        "/api/users", json={"username": "x1y", "password": "una-clave-larga-1", "role": "root"}, headers=admin
    )
    assert bad_role.status_code == 422
    own = client.patch(f"/api/users/{users['admin']}", json={"role": "analyst"}, headers=admin)
    assert own.status_code == 409
    assert client.patch("/api/users/9999", json={"active": False}, headers=admin).status_code == 404


def test_change_own_password(client, auth, synth):
    headers = auth("analyst")
    wrong = client.post(
        "/api/auth/password", json={"current_password": "no-es", "new_password": "nueva-clave-larga-9"}, headers=headers
    )
    assert wrong.status_code == 403
    ok = client.post(
        "/api/auth/password",
        json={"current_password": synth.password, "new_password": "nueva-clave-larga-9"}, headers=headers,
    )
    assert ok.status_code == 200
    assert client.post("/api/auth/login", json={"username": "analyst", "password": synth.password}).status_code == 401
    assert client.post(
        "/api/auth/login", json={"username": "analyst", "password": "nueva-clave-larga-9"}
    ).status_code == 200


def test_bootstrap_creates_admin_without_default_password(db, fast_hashing, monkeypatch, capsys):
    user = bootstrap.create_admin(db, "Jefa", "una-clave-bien-larga")
    db.commit()
    assert user.role == "admin" and user.username == "jefa"
    assert user.password_hash.startswith("$argon2")
    assert security.verify_password(user.password_hash, "una-clave-bien-larga")
    assert _actions(db) == ["user.bootstrap"]

    for bad_args in (("jefa", "una-clave-bien-larga"), ("otra", "corta"), ("otra", "")):
        try:
            bootstrap.create_admin(db, *bad_args)
        except bootstrap.BootstrapError:
            db.rollback()
        else:
            raise AssertionError(f"debió fallar: {bad_args}")
    assert db.execute(select(User)).scalars().all() == [user]

    # Sin usuario o sin clave el comando falla: no hay valores por defecto
    monkeypatch.delenv("ALEPH_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ALEPH_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert bootstrap.main([]) == 2
    assert bootstrap.main(["--username", "alguien"]) == 2
    assert "Falta la contraseña" in capsys.readouterr().err
    assert "ALEPH_ADMIN_PASSWORD" in inspect.getsource(bootstrap)
