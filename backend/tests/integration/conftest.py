"""Fixtures de integración: caso de demostración sembrado en SQLite en memoria, sin red.

- `demo`: el sembrador real (`aleph.demo.seed`) corre una vez por módulo sobre una base en memoria.
- `client`: TestClient con las dependencias de salida (conectores, herramientas, LLM, proveedores CTI)
  apagadas; los tests que necesitan respuestas las reemplazan con `httpx.MockTransport`.
- `token(username)`: token real para un usuario existente (no pasa por el login, así que es rápido).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from aleph.api import security
from aleph.api.services.outbound import enrichment_providers, llm_http, outbound_http
from aleph.core.config import Settings, get_settings
from aleph.core.db import get_session, init_db, make_engine
from aleph.core.models import Case, User
from aleph.demo import seed as demo_seed
from aleph.demo import story
from aleph.main import app

DEMO_PASSWORD = "clave-demo-de-prueba-123"
ADMIN_PASSWORD = "clave-admin-de-prueba-123"
SECRET = "clave-solo-para-tests-0123456789abcdef"
DEMO_CASE_NAME = story.DEMO_CASE_NAME


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """Caso de demostración sembrado por el sembrador real, una vez por módulo."""
    data_dir = tmp_path_factory.mktemp("demo-data")
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        report = demo_seed.seed(session, data_dir=data_dir, demo_password=DEMO_PASSWORD,
                                admin_password=ADMIN_PASSWORD)
    yield SimpleNamespace(engine=engine, factory=factory, data_dir=data_dir, report=report,
                          case_id=report.case_id)
    engine.dispose()


@pytest.fixture
def settings(demo):
    return Settings(
        _env_file=None, database_url="sqlite:///:memory:", secret_key=SECRET,
        data_dir=str(demo.data_dir), llm_base_url="http://127.0.0.1:4000/v1", llm_model="modelo-de-prueba",
        llm_api_key="",
    )


def mock_crt_sh_rows() -> list[dict]:
    """Respuesta simulada de crt.sh para ejemplo.example: dos subdominios y un comodín."""
    return [
        {"name_value": "ejemplo.example\nwww.ejemplo.example\n*.ejemplo.example", "common_name": "ejemplo.example"},
        {"name_value": "mail.ejemplo.example", "common_name": "mail.ejemplo.example"},
    ]


def _unreachable(request: httpx.Request) -> httpx.Response:
    """Transporte de prueba que simula un host caído: cualquier llamada de salida falla sin red."""
    raise httpx.ConnectError("sin red en las pruebas", request=request)


def unreachable_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(_unreachable))


@pytest.fixture
def client(demo, settings):
    def override_session():
        with demo.factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    # Por defecto, ninguna llamada de salida llega a la red: los tests que las necesitan la reemplazan
    app.dependency_overrides[outbound_http] = unreachable_client
    app.dependency_overrides[llm_http] = unreachable_client
    app.dependency_overrides[enrichment_providers] = list
    from fastapi.testclient import TestClient

    yield TestClient(app)  # sin `with`: no corre el lifespan, no se toca ninguna base real
    app.dependency_overrides.clear()


@pytest.fixture
def token(demo, settings):
    """token("analista-demo") -> encabezados de autorización de un usuario existente."""

    def headers(username: str) -> dict:
        with demo.factory() as session:
            user = session.execute(select(User).where(User.username == username)).scalars().one()
            value, _ = security.create_token(user.id, user.role, settings)
        return {"Authorization": f"Bearer {value}"}

    return headers


@pytest.fixture
def demo_case(demo) -> int:
    with demo.factory() as session:
        case = session.execute(select(Case).where(Case.name == DEMO_CASE_NAME)).scalars().one()
        return case.id


@pytest.fixture
def mock_http():
    """mock_http(handler) -> httpx.AsyncClient sobre MockTransport; ningún pedido sale a la red."""
    clients: list[httpx.AsyncClient] = []

    def make(handler) -> httpx.AsyncClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(http)
        return http

    yield make


@pytest.fixture
def fresh_case(client, token, demo):
    """Caso nuevo y vacío, abierto por el analista (para los tests que escriben)."""
    response = client.post("/api/cases", headers=token("analista-demo"), json={
        "name": f"Caso de integración {datetime.now(UTC).timestamp():.0f}", "legal_basis": "Prueba automatizada.",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]
