"""Conectores de recolección. Importar este paquete registra todos los conectores.

Cada módulo define una subclase de Connector decorada con @register. Los módulos que empiezan con
guion bajo (_util) son ayudantes internos.
"""

from aleph.connectors import (  # noqa: F401 - cada import registra su conector
    bluesky,
    discord_export,
    domain_intel,
    generic_csv,
    github,
    instagram_export,
    mastodon,
    reddit,
    telegram_channel,
    username_search,
    x_api,
    x_archive,
)
from aleph.connectors.base import (
    Connector,
    ConnectorError,
    get_connector,
    list_connectors,
    register,
)

__all__ = [
    "Connector",
    "ConnectorError",
    "get_connector",
    "list_connectors",
    "register",
]
