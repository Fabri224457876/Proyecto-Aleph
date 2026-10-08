"""Archivo de datos de X: carpeta data/ con window.YTD, datos ya leídos y errores. Sin red."""

from datetime import UTC, datetime

import pytest
from connector_helpers import FIXTURES

from aleph.connectors.base import ConnectorError
from aleph.connectors.x_archive import XArchiveConnector

ARCHIVE = FIXTURES / "x_archive"


async def test_imports_archive_folder_with_normalized_posts():
    result = await XArchiveConnector().collect(path=str(ARCHIVE))
    (profile,) = result.profiles
    acc = profile.account
    assert acc.platform == "x" and acc.handle == "usuario_demo"
    assert acc.platform_uid == "111"
    assert acc.created_at_platform == datetime(2010, 6, 14, 14, 46, 39, tzinfo=UTC)
    assert acc.bio == "Cuenta de prueba para tests."
    assert acc.meta["location"] == "Ciudad Demo"

    posts = profile.posts
    assert [p.kind for p in posts] == ["original", "repost", "reply"]

    first = posts[0]
    assert first.platform_post_id == "1001"
    assert first.created_at == datetime(2018, 10, 10, 20, 19, 24, tzinfo=UTC)
    assert first.hashtags == ["osint"]  # de entities.hashtags[].text
    assert first.mentions == ["otro_user"]  # de user_mentions[].screen_name, en minúscula
    assert first.urls == ["https://ejemplo.example/nota"]  # expanded_url
    assert first.client == "Twitter Web App"  # HTML del campo source, convertido a texto
    assert first.lang == "es"

    repost = posts[1]
    assert repost.hashtags == ["cti"]
    assert repost.mentions == ["otro_user"]
    assert repost.created_at == datetime(2018, 10, 11, 8, tzinfo=UTC)

    reply = posts[2]
    assert reply.reply_to == "1001"
    assert reply.client == "Twitter for iPhone"
    assert reply.mentions == ["usuario_demo"]

    # Procedencia: un hash por archivo fuente.
    assert {f["name"] for f in result.raw["files"]} == {"tweets.js", "account.js", "profile.js"}
    assert all(len(f["sha256"]) == 64 for f in result.raw["files"])

    # El correo de la cuenta se marca como sensible y queda relacionado a la cuenta.
    email = next(e for e in result.entities if e.type == "email")
    assert email.label == "usuario.demo@correo.example"
    assert email.props["sensitive"] is True
    assert any(r.type == "declares" and r.dst_ref == "url:https://sitio.example.org"
               for r in result.relations)


async def test_accepts_already_read_text_or_lists():
    tweets_js = (ARCHIVE / "data" / "tweets.js").read_text(encoding="utf-8")
    account_js = (ARCHIVE / "data" / "account.js").read_text(encoding="utf-8")
    result = await XArchiveConnector().collect(
        data={"tweets": tweets_js, "account": account_js}
    )
    assert len(result.profiles[0].posts) == 3
    assert result.reference == "datos en memoria"


async def test_limit_truncates_the_import():
    result = await XArchiveConnector().collect(path=str(ARCHIVE), limit=2)
    assert len(result.profiles[0].posts) == 2


async def test_without_account_file_the_handle_must_be_given():
    tweets_js = (ARCHIVE / "data" / "tweets.js").read_text(encoding="utf-8")
    with pytest.raises(ConnectorError, match="handle"):
        await XArchiveConnector().collect(data={"tweets": tweets_js})

    result = await XArchiveConnector().collect(data={"tweets": tweets_js}, handle="@usuario_demo")
    assert result.profiles[0].account.handle == "usuario_demo"
    assert any("no hay account.js" in w for w in result.warnings)


async def test_missing_path_and_no_input_are_errors(tmp_path):
    with pytest.raises(ConnectorError, match="no existe"):
        await XArchiveConnector().collect(path=str(tmp_path / "no-existe"))
    with pytest.raises(ConnectorError, match="indicá"):
        await XArchiveConnector().collect()
