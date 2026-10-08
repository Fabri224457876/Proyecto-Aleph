"""CSV y JSONL genéricos: mapeo de columnas, listas, fechas, kind, IDs estables y avisos. Sin red."""

import json
from datetime import UTC, datetime

import pytest

from aleph.connectors.base import ConnectorError
from aleph.connectors.generic_csv import GenericCsvConnector

CSV = (
    "platform,handle,text,created_at,post_id,kind,reply_to,mentions,hashtags,urls,followers,"
    "display_name\n"
    "x,ana_demo,Hola #Tag a @Bob,2024-01-01T10:00:00Z,p1,,,bob;carol,,"
    "https://ejemplo.example/a;https://otro.example/b,12,Ana Demo\n"
    "x,ana_demo,Respuesta,2024-01-02T10:00:00+02:00,p2,reply,p1,,,,,\n"
    ",,sin handle,2024-01-03,p3,,,,,,,\n"
    "x,bob,Texto con fecha mala,no-es-fecha,,retweet,,,,,,\n"
)


async def test_csv_rows_become_accounts_and_posts_with_normalization():
    result = await GenericCsvConnector().collect(data=CSV)
    profiles = {p.account.handle: p for p in result.profiles}
    assert set(profiles) == {"ana_demo", "bob"}

    ana = profiles["ana_demo"]
    assert ana.account.platform == "x"
    assert ana.account.followers == 12
    assert ana.account.display_name == "Ana Demo"
    first, reply = ana.posts
    assert first.platform_post_id == "p1"
    assert first.created_at == datetime(2024, 1, 1, 10, tzinfo=UTC)
    assert first.mentions == ["bob", "carol"]  # columna con ";", en minúscula
    assert first.hashtags == ["tag"]  # columna vacía: se extrae del texto
    assert first.urls == ["https://ejemplo.example/a", "https://otro.example/b"]
    assert reply.kind == "reply" and reply.reply_to == "p1"
    assert reply.created_at == datetime(2024, 1, 2, 8, tzinfo=UTC)  # +02:00 pasado a UTC

    bob_post = profiles["bob"].posts[0]
    assert bob_post.created_at is None  # fecha no interpretable
    assert bob_post.kind == "original"  # "retweet" no es un kind válido: se infiere

    assert any("filas sin handle" in w for w in result.warnings)
    assert any("fechas no interpretables" in w for w in result.warnings)
    assert any("valores de kind no válidos" in w for w in result.warnings)


async def test_semicolon_separated_files_are_detected():
    result = await GenericCsvConnector().collect(data="platform;handle;text\nx;ana;Hola #Uno\n")
    (profile,) = result.profiles
    assert profile.account.handle == "ana"
    assert profile.posts[0].hashtags == ["uno"]


async def test_jsonl_with_a_bad_line_and_numeric_dates():
    jsonl = (
        '{"platform": "telegram", "handle": "canal", "text": "Hola @x #y", '
        '"created_at": 1714557600}\n'
        "NO ES JSON\n"
        '{"platform": "telegram", "handle": "canal", "text": "Segundo", '
        '"created_at": "2024-05-02T10:00:00Z", "post_id": "b2"}\n'
    )
    result = await GenericCsvConnector().collect(data=jsonl, format="jsonl")
    (profile,) = result.profiles
    assert profile.account.platform == "telegram"
    assert len(profile.posts) == 2
    first = profile.posts[0]
    assert first.created_at == datetime(2024, 5, 1, 10, tzinfo=UTC)
    assert first.mentions == ["x"] and first.hashtags == ["y"]
    assert any("líneas JSONL inválidas" in w for w in result.warnings)


async def test_mapping_renames_columns_and_sets_platform():
    result = await GenericCsvConnector().collect(
        data="usuario,contenido\nana,Texto #Mapa\n",
        mapping={"handle": "usuario", "text": "contenido"},
        platform="foro",
    )
    (profile,) = result.profiles
    assert profile.account.platform == "foro"
    assert profile.account.handle == "ana"
    assert profile.posts[0].hashtags == ["mapa"]


async def test_rows_without_post_id_get_stable_ids():
    first = await GenericCsvConnector().collect(data="platform,handle,text\nx,ana,Hola\n")
    second = await GenericCsvConnector().collect(data="platform,handle,text\nx,ana,Hola\n")
    id_a = first.profiles[0].posts[0].platform_post_id
    id_b = second.profiles[0].posts[0].platform_post_id
    assert id_a == id_b and id_a.startswith("row-")


async def test_file_with_utf8_bom_is_read_and_limit_applies(tmp_path):
    source = tmp_path / "datos.csv"
    source.write_text("﻿platform,handle,text\nx,ana,uno\nx,ana,dos\nx,ana,tres\n",
                      encoding="utf-8")
    result = await GenericCsvConnector().collect(path=str(source), limit=2)
    (profile,) = result.profiles
    assert profile.account.platform == "x"
    assert [p.text for p in profile.posts] == ["uno", "dos"]
    assert result.raw["files"][0]["name"] == "datos.csv"


async def test_empty_import_reports_the_mapping_problem():
    result = await GenericCsvConnector().collect(data="platform,handle,text\n")
    assert result.profiles == []
    assert any("revisá el mapeo" in w for w in result.warnings)


async def test_invalid_mapping_and_missing_input_are_errors(tmp_path):
    with pytest.raises(ConnectorError, match="mapping"):
        await GenericCsvConnector().collect(data=CSV, mapping="{no es json")
    with pytest.raises(ConnectorError, match="no existe"):
        await GenericCsvConnector().collect(path=str(tmp_path / "no.csv"))
    with pytest.raises(ConnectorError, match="indicá"):
        await GenericCsvConnector().collect()


async def test_list_of_dicts_is_accepted_directly():
    rows = [{"platform": "x", "handle": "ana", "text": "Hola"},
            {"platform": "x", "handle": "ana", "text": json.dumps("dos")}]
    result = await GenericCsvConnector().collect(data=rows)
    assert len(result.profiles) == 1 and len(result.profiles[0].posts) == 2
