"""Conector Telegram: vista web pública de canales (t.me/s/<canal>). Sólo canales públicos.

Fuente: la vista previa web de Telegram, sin autenticación. NO es una API oficial de Telegram, y
no hay documentación oficial de su HTML, así que los selectores (tgme_channel_info_*,
tgme_widget_message, tgme_widget_message_text, tgme_widget_message_forwarded_from,
tgme_widget_message_views, time[datetime]) están basados en la forma conocida de la página y
NO están verificados contra una página real en este entorno.

Paginación: t.me/s/<canal>?before=<id> devuelve publicaciones anteriores a ese número.
Los enlaces de invitación privados (t.me/+xxxx) se rechazan: el conector sólo lee canales públicos.
"""

import re
from typing import ClassVar

from aleph.connectors._util import (
    HttpSession,
    Node,
    RateLimited,
    anchor_hrefs,
    dedupe,
    empty_result,
    extract_hashtags,
    extract_mentions,
    extract_urls,
    parse_datetime,
    parse_html,
    positive_int,
    rate_limit_warning,
    require_text,
    utcnow,
)
from aleph.connectors.base import Connector, ConnectorError, register
from aleph.core.schemas import AccountProfile, AccountRecord, CollectionResult, PostRecord

CHANNEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_COUNT_RE = re.compile(r"^\s*(\d[\d.,]*)\s*([KMB]?)\s*$", re.IGNORECASE)
_SUBSCRIBER_WORDS = ("subscriber", "suscriptor", "member", "miembro")


def _clean_channel(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/(s/)?", "", text)
    text = text.strip("/").lstrip("@")
    if not CHANNEL_RE.match(text):
        raise ConnectorError(
            "canal inválido: sólo se aceptan nombres de canales públicos "
            "(los enlaces de invitación privados no se leen)"
        )
    return text


def _parse_count(text: str) -> int | None:
    """'1.2K' -> 1200, '15.3M' -> 15300000, '1,234' -> 1234."""
    match = _COUNT_RE.match(text or "")
    if not match:
        return None
    number, suffix = match.group(1), match.group(2).upper()
    try:
        if suffix:
            multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
            return round(float(number.replace(",", ".")) * multiplier)
        return int(re.sub(r"[.,]", "", number))
    except ValueError:
        return None


def _subscribers(root: Node) -> int | None:
    for counter in root.find_all(lambda n: n.has_class("tgme_channel_info_counter")):
        labels = counter.find_all(lambda n: n.has_class("counter_type"))
        values = counter.find_all(lambda n: n.has_class("counter_value"))
        label = labels[0].text().lower() if labels else ""
        if values and any(word in label for word in _SUBSCRIBER_WORDS):
            return _parse_count(values[0].text())
    return None


def _messages(root: Node, channel: str) -> list[tuple[int, PostRecord]]:
    found: list[tuple[int, PostRecord]] = []
    for node in root.find_all(
        lambda n: n.has_class("tgme_widget_message") and "data-post" in n.attrs
    ):
        data_post = node.attrs["data-post"]  # "canal/123"
        number_text = data_post.rsplit("/", 1)[-1]
        if not number_text.isdigit():
            continue
        number = int(number_text)

        body_nodes = node.find_all(lambda n: n.has_class("tgme_widget_message_text"))
        body = body_nodes[0] if body_nodes else None
        text = body.text() if body is not None else ""
        urls = anchor_hrefs(body) if body is not None else []
        urls = [u for u in urls if "t.me/s/" not in u]  # enlaces de búsqueda de hashtags

        times = node.find_all(lambda n: n.tag == "time" and "datetime" in n.attrs)
        created = parse_datetime(times[0].attrs["datetime"]) if times else None

        views_nodes = node.find_all(lambda n: n.has_class("tgme_widget_message_views"))
        views = _parse_count(views_nodes[0].text()) if views_nodes else None

        forwarded = node.find_all(lambda n: n.has_class("tgme_widget_message_forwarded_from"))
        forwarded_name = None
        if forwarded:
            names = forwarded[0].find_all(
                lambda n: n.has_class("tgme_widget_message_forwarded_from_name")
            )
            forwarded_name = names[0].text() if names else forwarded[0].text() or None

        found.append(
            (
                number,
                PostRecord(
                    platform_post_id=data_post,
                    text=text,
                    created_at=created,
                    kind="repost" if forwarded else "original",
                    mentions=extract_mentions(text),
                    hashtags=extract_hashtags(text),
                    urls=dedupe(urls + extract_urls(text)),
                    client="",
                    meta={
                        "views": views,
                        "forwarded_from": forwarded_name,
                        "post_number": number,
                        "channel": channel,
                    },
                ),
            )
        )
    return found


@register
class TelegramChannelConnector(Connector):
    name = "telegram_channel"
    title = "Telegram (canal público, vista web)"
    mode = "live"
    params: ClassVar[dict[str, str]] = {
        "handle": "Nombre del canal público, sin @ (p. ej. el de t.me/s/<canal>)",
        "limit": "Máximo de publicaciones a recolectar (por defecto 100)",
    }

    async def collect(self, *, handle: str, limit: int = 100) -> CollectionResult:
        channel = _clean_channel(require_text(handle, "handle"))
        limit = positive_int(limit, "limit", 100)
        base = f"https://t.me/s/{channel}"
        reference = f"https://t.me/{channel}"
        warnings: list[str] = []
        raw_pages: list[str] = []

        async with HttpSession(self.client, headers={"Accept": "text/html"}) as http:
            try:
                first = await http.get_text(base)
            except RateLimited as exc:
                return empty_result(self.name, reference, [rate_limit_warning(exc)], raw_pages)
            if first is None:
                raise ConnectorError(f"canal no encontrado o sin vista pública: {channel}")
            raw_pages.append(first)
            root = parse_html(first)

            titles = root.find_all(lambda n: n.has_class("tgme_channel_info_header_title"))
            if not titles:
                raise ConnectorError(f"canal no encontrado o sin vista pública: {channel}")
            descriptions = root.find_all(lambda n: n.has_class("tgme_channel_info_description"))
            subscribers = _subscribers(root)

            found = _messages(root, channel)
            collected = {number: post for number, post in found}
            try:
                while len(collected) < limit and found:
                    before = min(number for number, _ in found)
                    if before <= 1:
                        break
                    page = await http.get_text(base, params={"before": before})
                    if page is None:
                        break
                    raw_pages.append(page)
                    found = _messages(parse_html(page), channel)
                    fresh = [(n, p) for n, p in found if n not in collected]
                    if not fresh:
                        break
                    collected.update({n: p for n, p in fresh})
            except RateLimited as exc:
                warnings.append(rate_limit_warning(exc))
            except ConnectorError as exc:
                warnings.append(f"publicaciones incompletas: {exc}")

        posts = [collected[n] for n in sorted(collected, reverse=True)][:limit]
        if not posts:
            warnings.append("el canal no muestra publicaciones en su vista pública")
        title = titles[0].text()
        account = AccountRecord(
            platform="telegram",
            handle=channel,
            display_name=title,
            bio=descriptions[0].text() if descriptions else "",
            url=reference,
            followers=subscribers,
            meta={"entity": "channel", "preview_url": base},
        )
        return CollectionResult(
            connector=self.name,
            reference=reference,
            retrieved_at=utcnow(),
            profiles=[AccountProfile(account=account, posts=posts)],
            warnings=warnings,
            raw=raw_pages,
        )
