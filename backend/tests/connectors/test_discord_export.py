"""DiscordChatExporter (JSON por canal): un AccountProfile por autor, respuestas y adjuntos. Sin red."""

import json
import shutil
from datetime import UTC, datetime

import pytest
from connector_helpers import FIXTURES

from aleph.connectors.base import ConnectorError
from aleph.connectors.discord_export import DiscordExportConnector

EXPORT = FIXTURES / "discord_export.json"


async def test_builds_one_profile_per_author():
    result = await DiscordExportConnector().collect(path=str(EXPORT))
    by_handle = {p.account.handle: p for p in result.profiles}
    assert set(by_handle) == {"ana_demo", "bob"}

    ana = by_handle["ana_demo"]
    assert ana.account.platform == "discord"
    assert ana.account.platform_uid == "1"
    assert ana.account.display_name == "Ana"
    # El mensaje de sistema (RecipientAdd, sin contenido) se descarta.
    assert [p.platform_post_id for p in ana.posts] == ["1001", "1004"]

    first = ana.posts[0]
    assert first.kind == "original"
    assert first.text == "Hola #general, pregunta para @bob"
    assert first.hashtags == ["general"]
    assert first.mentions == ["bob"]  # de mentions[].name
    assert first.urls == ["https://embed.example/nota"]  # enlace de embed
    assert first.created_at == datetime(2024, 4, 1, 12, tzinfo=UTC)
    assert first.meta["channel"] == "Servidor Demo/#general"

    attachment_only = ana.posts[1]
    assert attachment_only.urls == []  # los adjuntos quedan en meta, no como enlaces del autor
    assert attachment_only.meta["attachments"] == [
        {"filename": "img.png", "url": "https://cdn.example/img.png"}
    ]

    bob = by_handle["bob"].posts
    assert len(bob) == 1
    assert bob[0].kind == "reply"
    assert bob[0].reply_to == "1001"
    assert bob[0].urls == ["https://ejemplo.example/a"]
    assert result.warnings == []


async def test_data_argument_matches_the_file():
    data = json.loads(EXPORT.read_text(encoding="utf-8"))
    from_file = await DiscordExportConnector().collect(path=str(EXPORT))
    from_data = await DiscordExportConnector().collect(data=data)
    assert [p.account.handle for p in from_data.profiles] == [
        p.account.handle for p in from_file.profiles
    ]
    assert sum(len(p.posts) for p in from_data.profiles) == 3


async def test_limit_stops_after_that_many_messages():
    result = await DiscordExportConnector().collect(path=str(EXPORT), limit=2)
    assert sum(len(p.posts) for p in result.profiles) == 2


async def test_overlapping_exports_of_the_same_channel_do_not_duplicate(tmp_path):
    shutil.copy(EXPORT, tmp_path / "exportacion_1.json")
    shutil.copy(EXPORT, tmp_path / "exportacion_2.json")
    result = await DiscordExportConnector().collect(path=str(tmp_path))
    assert sum(len(p.posts) for p in result.profiles) == 3
    assert len(result.raw["files"]) == 2


async def test_invalid_json_and_missing_input_are_errors(tmp_path):
    broken = tmp_path / "roto.json"
    broken.write_text("{no es json", encoding="utf-8")
    with pytest.raises(ConnectorError, match="ilegible"):
        await DiscordExportConnector().collect(path=str(broken))
    with pytest.raises(ConnectorError, match="no existe"):
        await DiscordExportConnector().collect(path=str(tmp_path / "no-existe.json"))
    with pytest.raises(ConnectorError, match="indicá"):
        await DiscordExportConnector().collect()
