"""Lo que SQLite deja pasar y PostgreSQL no: las pruebas corren en SQLite, producción en PostgreSQL.

- Largo de columna: SQLite acepta un texto más largo que su VARCHAR(n); PostgreSQL lo rechaza y
  aborta la transacción. Se revisan todas las columnas con largo.
- Orden con NULL, `lower()` con letras no ASCII y valores que no son JSON.
"""

from datetime import UTC, datetime

from aleph.api.services.ingest import ingest_collection
from aleph.core.db import Base
from aleph.core.models import Account, Case, Post, User
from aleph.core.schemas import AccountProfile, AccountRecord, CollectionResult, PostRecord
from sqlalchemy import String, select


def _overflowing_columns(session) -> list[str]:
    problems = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            length = getattr(column.type, "length", None)
            if not isinstance(column.type, String) or not length:
                continue
            for (value,) in session.execute(select(column)):
                if isinstance(value, str) and len(value) > length:
                    problems.append(f"{table.name}.{column.name} ({len(value)} > {length})")
    return problems


def test_ingest_fits_external_values_to_columns(db, tmp_path):
    user = User(username="ana", password_hash="x", role="analyst")
    db.add(user)
    db.flush()
    case = Case(name="Caso", legal_basis="Prueba sintética", created_by=user.id)
    db.add(case)
    db.commit()

    long_handle = "h" * 300
    record = AccountRecord(platform="P" * 50, handle=long_handle, platform_uid="u" * 300,
                           display_name="d" * 600, avatar_phash="f" * 100)
    posts = [PostRecord(platform_post_id="p" * 250, text="hola", lang="zh-Hant-TW", reply_to="r" * 300,
                        client="c" * 150, created_at=datetime(2026, 3, 1, tzinfo=UTC))]
    result = CollectionResult(connector="c" * 80, reference="archivo.csv", retrieved_at=datetime.now(UTC),
                              profiles=[AccountProfile(account=record, posts=posts)])

    first = ingest_collection(db, case.id, result, user.id, data_dir=tmp_path)
    db.commit()
    assert first.accounts_created == 1 and first.posts_created == 1
    assert _overflowing_columns(db) == []

    # Repetir la misma recolección no duplica ni marca cambios: lo recortado se compara recortado.
    again = ingest_collection(db, case.id, result, user.id, data_dir=tmp_path)
    db.commit()
    assert (again.accounts_created, again.posts_created, again.posts_updated, again.posts_unchanged) == (0, 0, 0, 1)
    assert again.source_id == first.source_id
    assert db.scalar(select(Account.handle)) == "h" * 200
    assert db.scalar(select(Post.lang)) == "zh-Hant"


def test_undated_posts_go_last_in_both_orders(client, auth, make_case):
    """PostgreSQL pone los NULL primero en orden descendente y SQLite al final: se fija el criterio."""
    case_id = make_case()
    base, headers = f"/api/cases/{case_id}", auth("analyst")
    posts = [{"platform_post_id": "sin-fecha", "text": "sin fecha"},
             {"platform_post_id": "vieja", "text": "vieja", "created_at": "2026-01-01T00:00:00Z"},
             {"platform_post_id": "nueva", "text": "nueva", "created_at": "2026-02-01T00:00:00Z"}]
    body = {"connector": "generic_csv", "reference": "x.csv", "retrieved_at": "2026-03-01T00:00:00Z",
            "profiles": [{"account": {"platform": "generic", "handle": "alfa"}, "posts": posts}]}
    summary = client.post(f"{base}/collections", json=body, headers=headers).json()
    account_id = summary["account_ids"][0]
    for order, expected in (("desc", ["nueva", "vieja", "sin-fecha"]), ("asc", ["vieja", "nueva", "sin-fecha"])):
        page = client.get(f"{base}/accounts/{account_id}/posts", params={"order": order}, headers=headers).json()
        assert [p["platform_post_id"] for p in page["items"]] == expected, order


def test_reingest_with_non_ascii_uppercase_handle(client, auth, make_case):
    """`lower()` de la base tiene que plegar Unicode igual que Python, o la deduplicación falla."""
    case_id = make_case()
    base, headers = f"/api/cases/{case_id}", auth("analyst")
    body = {"connector": "generic_csv", "reference": "x.csv", "retrieved_at": "2026-03-01T00:00:00Z",
            "profiles": [{"account": {"platform": "generic", "handle": "ÑANDÚ_ÉLITE"},
                          "posts": [{"platform_post_id": "1", "text": "hola"}]}],
            "entities": [{"type": "person", "label": "ÁLVAREZ Sintético"}]}
    first = client.post(f"{base}/collections", json=body, headers=headers)
    assert first.status_code == 201, first.text
    body["profiles"][0]["account"]["handle"] = "ñandú_élite"
    body["entities"][0]["label"] = "álvarez sintético"
    second = client.post(f"{base}/collections", json=body, headers=headers)
    assert second.status_code == 201, second.text
    assert (second.json()["accounts_created"], second.json()["entities_created"]) == (0, 0)
    found = client.get(f"{base}/entities", params={"q": "álvarez"}, headers=headers).json()
    assert found["total"] == 1


def test_ingest_stores_connector_objects_as_json(db, tmp_path):
    """Un conector puede dejar fechas o tuplas en `meta` y `props`: se guardan como JSON puro."""
    from aleph.core.models import Entity
    from aleph.core.schemas import EntityRecord

    user = User(username="ana", password_hash="x", role="analyst")
    db.add(user)
    db.flush()
    case = Case(name="Caso", legal_basis="Prueba sintética", created_by=user.id)
    db.add(case)
    db.commit()
    when = datetime(2026, 3, 1, 12, tzinfo=UTC)
    profile = AccountProfile(
        account=AccountRecord(platform="generic", handle="alfa", meta={"visto": when, "par": (1, 2)}),
        posts=[PostRecord(platform_post_id="1", text="hola", meta={"editado": when})],
    )
    result = CollectionResult(connector="prueba", reference="r", retrieved_at=when, profiles=[profile],
                              entities=[EntityRecord(type="domain", label="ejemplo.test", props={"alta": when})])
    ingest_collection(db, case.id, result, user.id, data_dir=tmp_path)
    db.commit()
    db.expire_all()
    assert db.scalar(select(Account.meta))["visto"] == "2026-03-01 12:00:00+00:00"
    assert db.scalar(select(Account.meta))["par"] == [1, 2]
    assert db.scalar(select(Post.meta)) == {"editado": "2026-03-01 12:00:00+00:00"}
    assert db.scalar(select(Entity.props).where(Entity.type == "domain")) == {"alta": "2026-03-01 12:00:00+00:00"}
    again = ingest_collection(db, case.id, result, user.id, data_dir=tmp_path)
    assert (again.posts_unchanged, again.posts_updated) == (1, 0)


def test_out_of_range_numbers_are_invalid_input_not_server_errors(client, auth, make_case):
    """Un entero que no entra en la columna es un dato inválido (422), no un 500."""
    case_id = make_case()
    headers = auth("analyst")
    huge = 10**20
    responses = [
        client.get(f"/api/cases/{case_id}/entities", params={"offset": huge}, headers=headers),
        client.post(f"/api/cases/{case_id}/sections", json={"name": "x", "position": huge}, headers=headers),
        client.get(f"/api/cases/{case_id}/entities/{huge}", headers=headers),
        client.get(f"/api/cases/{huge}", headers=headers),
        client.get(f"/api/jobs/{huge}", headers=headers),
    ]
    assert [r.status_code for r in responses] == [422] * 5, [r.text for r in responses]
    assert "fuera del rango" in responses[2].json()["detail"]
    # Después de un pedido rechazado, la API sigue respondiendo y escribiendo con normalidad
    assert client.post(f"/api/cases/{case_id}/sections", json={"name": "Nuevo"}, headers=headers).status_code == 201
