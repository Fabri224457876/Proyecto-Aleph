"""Exportación oficial de Instagram (JSON): perfil, publicaciones, comentarios, listas y mojibake.

Sin red. Los archivos de fixtures/instagram/ son sintéticos y reproducen la estructura de carpetas
habitual de la exportación; el esquema no pudo verificarse contra documentación oficial.
"""

import json
from datetime import UTC, datetime

import pytest
from connector_helpers import FIXTURES

from aleph.connectors.base import ConnectorError
from aleph.connectors.instagram_export import InstagramExportConnector

EXPORT = FIXTURES / "instagram"


async def test_imports_export_with_mojibake_fixed_and_relations():
    result = await InstagramExportConnector().collect(path=str(EXPORT))
    (profile,) = result.profiles
    acc = profile.account
    assert acc.platform == "instagram" and acc.handle == "usuario.demo"
    assert acc.display_name == "José Demo"  # "JosÃ©" corregido
    assert acc.bio == "Fotos de prueba 😀"  # emoji recuperado desde latin-1
    assert acc.url == "https://www.instagram.com/usuario.demo/"
    assert acc.follower_handles == ["persona.demo", "otra.persona"]
    assert acc.following_handles == ["persona.demo"]
    assert acc.followers == 2 and acc.following == 1

    posts = profile.posts
    assert [p.kind for p in posts] == ["original", "original", "reply"]

    first = posts[0]
    assert first.platform_post_id == "media/posts/202405/foto1.jpg"
    assert first.text == "Café con #Osint y @fuente.demo"
    assert first.created_at == datetime(2024, 5, 1, 10, tzinfo=UTC)  # epoch en segundos
    assert first.hashtags == ["osint"]
    assert first.mentions == ["fuente.demo"]

    second = posts[1]
    assert second.meta["media_count"] == 2
    assert second.urls == ["https://ejemplo.example/a"]
    assert second.created_at == datetime(2024, 5, 2, 10, tzinfo=UTC)

    comment = posts[2]
    assert comment.text == "Muy bueno ¡gracias!"
    assert comment.meta["post_owner"] == "otra.cuenta.demo"
    assert comment.reply_to == ""  # la exportación no trae el id de la publicación

    email = next(e for e in result.entities if e.type == "email")
    assert email.label == "usuario.demo@correo.example"
    assert email.props["sensitive"] is True
    assert any(r.type == "declares" and r.props["field"] == "website"
               for r in result.relations)
    assert result.warnings == []


async def test_accepts_already_parsed_documents():
    def load(*parts):
        return json.loads((EXPORT.joinpath(*parts)).read_text(encoding="utf-8"))

    data = {
        "profile": load("personal_information", "personal_information.json"),
        "posts": load("your_instagram_activity", "media", "posts_1.json"),
        "comments": load("your_instagram_activity", "comments", "post_comments_1.json"),
        "followers": load("connections", "followers_and_following", "followers_1.json"),
        "following": load("connections", "followers_and_following", "following.json"),
    }
    result = await InstagramExportConnector().collect(data=data)
    assert len(result.profiles[0].posts) == 3
    assert result.profiles[0].account.display_name == "José Demo"


async def test_limit_counts_posts_and_comments_together():
    result = await InstagramExportConnector().collect(path=str(EXPORT), limit=2)
    assert len(result.profiles[0].posts) == 2


async def test_handle_is_required_when_profile_is_missing():
    with pytest.raises(ConnectorError, match="handle"):
        await InstagramExportConnector().collect(data={"posts": []})
    result = await InstagramExportConnector().collect(data={"posts": []}, handle="otro_usuario")
    assert result.profiles[0].account.handle == "otro_usuario"
    assert any("no hay personal_information.json" in w for w in result.warnings)


async def test_missing_path_is_an_error(tmp_path):
    with pytest.raises(ConnectorError, match="no existe"):
        await InstagramExportConnector().collect(path=str(tmp_path / "no-existe"))
