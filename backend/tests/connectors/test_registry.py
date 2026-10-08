"""Registro de conectores: los doce están, con sus metadatos y sin importar nada de red."""

import pytest

from aleph.connectors.base import ConnectorError, get_connector, list_connectors

EXPECTED_MODES = {
    "bluesky": "live",
    "mastodon": "live",
    "reddit": "live",
    "github": "live",
    "telegram_channel": "live",
    "x_api": "live",
    "username_search": "live",
    "domain_intel": "live",
    "x_archive": "import",
    "instagram_export": "import",
    "discord_export": "import",
    "generic_csv": "import",
}


def test_all_twelve_connectors_are_registered_with_their_mode():
    assert {cls.name: cls.mode for cls in list_connectors()} == EXPECTED_MODES


def test_each_connector_declares_title_params_and_requires():
    for cls in list_connectors():
        assert cls.title, cls.name
        assert cls.params and all(
            isinstance(k, str) and isinstance(v, str) for k, v in cls.params.items()
        ), cls.name
        assert isinstance(cls.requires, list), cls.name


def test_x_api_requires_the_bearer_token_setting():
    assert get_connector("x_api").requires == ["x_bearer_token"]


def test_unknown_connector_raises_connector_error():
    with pytest.raises(ConnectorError):
        get_connector("no_existe")
