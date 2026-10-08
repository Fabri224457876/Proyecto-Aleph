"""Telegram (vista web pública t.me/s/<canal>): paginación con before, reenvíos y solo canales públicos.

El HTML es una reconstrucción de la vista pública con sus clases conocidas (tgme_*); no está
verificado contra una página real de Telegram en este entorno.
"""

from datetime import UTC, datetime

import httpx
import pytest
from connector_helpers import NO_KEYS, client_for, no_sleep

from aleph.connectors.base import ConnectorError
from aleph.connectors.telegram_channel import TelegramChannelConnector


def _page(messages: str, channel_info: bool = True) -> str:
    if channel_info:
        info = (
            '<div class="tgme_channel_info_header_title"><span dir="auto">Canal de Ejemplo</span></div>'
            '<div class="tgme_channel_info_description">Descripción del canal</div>'
            '<div class="tgme_channel_info_counters"><div class="tgme_channel_info_counter">'
            '<span class="counter_value">1.2K</span> <span class="counter_type">subscribers</span>'
            "</div></div>"
        )
    else:
        info = '<div class="tgme_page_title">Contacto</div>'
    return (
        "<html><body>"
        f'<div class="tgme_channel_info">{info}</div>'
        f'<div class="tgme_channel_history js-message_history">{messages}</div>'
        "</body></html>"
    )


def _message(
    number: int,
    text_html: str | None,
    *,
    time: str,
    views: str,
    forwarded: tuple[str, str] | None = None,
    channel: str = "canal_ejemplo",
) -> str:
    fwd = ""
    if forwarded:
        name, slug = forwarded
        fwd = (
            '<div class="tgme_widget_message_forwarded_from">Forwarded from '
            f'<a class="tgme_widget_message_forwarded_from_name" href="https://t.me/{slug}">'
            f"{name}</a></div>"
        )
    text = ""
    if text_html is not None:
        text = f'<div class="tgme_widget_message_text js-message_text" dir="auto">{text_html}</div>'
    return (
        '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
        '<div class="tgme_widget_message text_not_supported_wrap js-widget_message" '
        f'data-post="{channel}/{number}">'
        f"{fwd}{text}"
        '<div class="tgme_widget_message_footer compact js-message_footer">'
        '<div class="tgme_widget_message_info short js-message_info">'
        f'<span class="tgme_widget_message_views">{views}</span>'
        '<span class="tgme_widget_message_meta">'
        f'<a class="tgme_widget_message_date" href="https://t.me/{channel}/{number}">'
        f'<time datetime="{time}" class="time">09:30</time></a></span>'
        "</div></div></div></div>"
    )


PAGE_1 = _page(
    _message(103, 'Nuevo informe #CTI con @fuente_demo <a href="https://sitio.example.org/informe">'
                  "sitio.example.org/informe</a>",
             time="2024-05-03T09:30:00+00:00", views="2.5K")
    + _message(102, "Reenvío de otro canal", time="2024-05-02T08:00:00+00:00", views="900",
               forwarded=("Otro Canal", "otro_canal"))
    + _message(101, None, time="2024-05-01T07:00:00+00:00", views="15.3K")  # sólo medio
)
PAGE_2 = _page(
    _message(100, "Más antiguo #osint", time="2024-04-30T06:00:00+00:00", views="1K")
    + _message(99, "Otro #OSINT", time="2024-04-29T06:00:00+00:00", views="12")
)
EMPTY_PAGE = _page("")


def _handler(log: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        if request.url.path == "/s/nadie":
            return httpx.Response(404)
        before = request.url.params.get("before")
        if before is None:
            body = PAGE_1
        elif before == "101":
            body = PAGE_2
        else:
            body = EMPTY_PAGE
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return handler


async def test_reads_public_preview_with_before_pagination():
    log: list[httpx.Request] = []
    result = await TelegramChannelConnector(
        client=client_for(_handler(log)), settings=NO_KEYS
    ).collect(handle="https://t.me/s/canal_ejemplo", limit=10)

    (profile,) = result.profiles
    acc = profile.account
    assert acc.platform == "telegram" and acc.handle == "canal_ejemplo"
    assert acc.display_name == "Canal de Ejemplo"
    assert acc.bio == "Descripción del canal"
    assert acc.followers == 1200  # 1.2K suscriptores

    numbers = [int(p.platform_post_id.split("/")[1]) for p in profile.posts]
    assert numbers == [103, 102, 101, 100, 99]

    first = profile.posts[0]
    assert first.kind == "original"
    assert first.text == "Nuevo informe #CTI con @fuente_demo sitio.example.org/informe"
    assert first.hashtags == ["cti"]
    assert first.mentions == ["fuente_demo"]
    assert "https://sitio.example.org/informe" in first.urls  # expandido desde el href
    assert first.created_at == datetime(2024, 5, 3, 9, 30, tzinfo=UTC)
    assert first.meta["views"] == 2500

    forwarded = profile.posts[1]
    assert forwarded.kind == "repost"
    assert forwarded.meta["forwarded_from"] == "Otro Canal"

    assert profile.posts[2].text == ""  # medio sin texto
    assert profile.posts[3].hashtags == ["osint"]

    # Paginación: la primera página, luego before=<menor número>, y una vacía que corta.
    assert [r.url.params.get("before") for r in log] == [None, "101", "99"]


async def test_private_or_unknown_channels_are_rejected():
    with pytest.raises(ConnectorError, match="sin vista pública"):
        await TelegramChannelConnector(client=client_for(_handler([])), settings=NO_KEYS).collect(
            handle="nadie"
        )


async def test_invite_links_are_not_read():
    with pytest.raises(ConnectorError, match="sólo se aceptan"):
        await TelegramChannelConnector(client=client_for(_handler([])), settings=NO_KEYS).collect(
            handle="https://t.me/+ABCDEF12"
        )


async def test_page_without_channel_header_is_not_a_channel():
    def handler(request):
        return httpx.Response(200, text=_page("", channel_info=False))

    with pytest.raises(ConnectorError, match="sin vista pública"):
        await TelegramChannelConnector(client=client_for(handler), settings=NO_KEYS).collect(
            handle="canal_ejemplo"
        )


async def test_429_on_first_page_is_retried(monkeypatch):
    waits = no_sleep(monkeypatch)
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        if hits["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, text=PAGE_1)

    result = await TelegramChannelConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="canal_ejemplo", limit=3
    )
    assert waits == [1.0]
    assert len(result.profiles[0].posts) == 3


async def test_429_over_cap_on_next_page_keeps_first_page(monkeypatch):
    waits = no_sleep(monkeypatch)

    def handler(request):
        if request.url.params.get("before") is None:
            return httpx.Response(200, text=PAGE_1)
        return httpx.Response(429, headers={"Retry-After": "999"})

    result = await TelegramChannelConnector(client=client_for(handler), settings=NO_KEYS).collect(
        handle="canal_ejemplo", limit=10
    )
    assert waits == []
    assert len(result.profiles[0].posts) == 3
    assert any("Límite de tasa" in w for w in result.warnings)
