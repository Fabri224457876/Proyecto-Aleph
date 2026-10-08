"""Fixtures de la API: base SQLite en memoria, sin red, con el motor MENARD simulado."""

from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from aleph.api import security
from aleph.core.config import Settings, get_settings
from aleph.core.db import get_session, init_db, make_engine
from aleph.core.models import User
from aleph.core.schemas import (
    AccountProfile,
    AccountRecord,
    ClusterResult,
    CollectionResult,
    EntityRecord,
    Evidence,
    MenardReport,
    PairResult,
    PostRecord,
    RelationRecord,
    SignalResult,
)
from aleph.main import app

PASSWORD = "clave-de-prueba-123"
LEGAL = "Investigación de prueba autorizada por resolución sintética 1/2026."


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    monkeypatch.setattr(security, "_hasher", PasswordHasher(time_cost=1, memory_cost=64, parallelism=1))
    monkeypatch.setattr(security, "_dummy_hash", None)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None, database_url="sqlite:///:memory:", secret_key="clave-solo-para-tests-0123456789abcdef",
        data_dir=str(tmp_path / "data"),
    )


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory, settings):
    def override_session():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    # Sin `with`: no corre el lifespan, así no se toca la base real del desarrollador
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def users(session_factory, fast_hashing):
    with session_factory() as session:
        created = {}
        for role in ("admin", "analyst", "auditor"):
            user = User(username=role, password_hash=security.hash_password(PASSWORD), role=role)
            session.add(user)
            created[role] = user
        session.commit()
        return {role: user.id for role, user in created.items()}


@pytest.fixture
def auth(users, settings):
    """auth("analyst") -> encabezados con un token válido para ese rol."""

    def headers(role: str) -> dict:
        token, _ = security.create_token(users[role], role, settings)
        return {"Authorization": f"Bearer {token}"}

    return headers


@pytest.fixture
def make_case(client, auth):
    def create(name: str = "Caso de prueba", role: str = "analyst", **extra) -> int:
        body = {"name": name, "legal_basis": LEGAL, **extra}
        response = client.post("/api/cases", json=body, headers=auth(role))
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return create


def make_profile(handle: str, n_posts: int = 3, platform: str = "bluesky", start_day: int = 1) -> AccountProfile:
    base = datetime(2026, 3, start_day, 12, 0, tzinfo=UTC)
    return AccountProfile(
        account=AccountRecord(
            platform=platform, handle=handle, display_name=handle.title(), bio=f"Cuenta sintética {handle}",
            followers=10, following_handles=["otra_cuenta"],
        ),
        posts=[
            PostRecord(
                platform_post_id=f"{handle}-{i}", text=f"publicación sintética {i} de {handle} sobre el puerto",
                created_at=base + timedelta(hours=i), hashtags=["prueba"],
            )
            for i in range(n_posts)
        ],
    )


def make_collection(handles=("alfa", "beta"), n_posts: int = 3, with_graph: bool = True) -> CollectionResult:
    profiles = [make_profile(h, n_posts) for h in handles]
    entities, relations = [], []
    if with_graph:
        entities = [EntityRecord(type="domain", label="ejemplo.test", ref="d1")]
        relations = [RelationRecord(src_ref=profiles[0].key, dst_ref="d1", type="shares_domain", confidence=0.8)]
    return CollectionResult(
        connector="fixture", reference="synthetic://lote-1", retrieved_at=datetime(2026, 3, 5, tzinfo=UTC),
        profiles=profiles, entities=entities, relations=relations, raw={"lote": 1, "cuentas": list(handles)},
    )


def fake_analyze(profiles: list[AccountProfile]) -> MenardReport:
    """Motor MENARD simulado: puntaje alto solo para el par alfa/beta."""
    pairs = []
    for i, a in enumerate(profiles):
        for b in profiles[i + 1:]:
            pair = {a.account.handle.lower(), b.account.handle.lower()}
            score = 0.91 if pair == {"alfa", "beta"} else 0.12
            pairs.append(PairResult(
                a=a.key, b=b.key, score=score, confidence="alta" if score > 0.8 else "baja",
                summary="Hipótesis de mismo operador (simulada).",
                signals=[SignalResult(
                    name="stylometry.char_ngrams", family="stylometry", score=score, explanation="Simulada",
                    evidence=[Evidence(description="Misma muletilla", a="holaaa", b="holaaa")],
                )],
            ))
    clusters = [ClusterResult(members=[p.key for p in profiles[:2]], cohesion=0.9, summary="Cluster simulado")]
    return MenardReport(pairs=pairs, clusters=clusters, skipped={}, params={"engine": "fake"})


@pytest.fixture
def menard_engine(monkeypatch):
    """Instala el motor simulado donde la API lo busca: `aleph.menard.analyze`."""
    import aleph.menard

    calls = []

    def analyze(profiles):
        calls.append(profiles)
        return fake_analyze(profiles)

    monkeypatch.setattr(aleph.menard, "analyze", analyze, raising=False)
    return calls


@pytest.fixture
def synth():
    """Fábricas de datos sintéticos (por fixture, para no depender de importar este conftest)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        profile=make_profile, collection=make_collection, analyze=fake_analyze, password=PASSWORD, legal=LEGAL
    )
