"""Búsqueda de un nombre de usuario en una lista curada de sitios públicos (sites.json).

Para cada sitio pide su página de perfil con GET y decide si la cuenta existe según el criterio
del sitio: código de estado o texto presente/ausente. Límites deliberados:
- No inicia sesión, no rota proxies ni User-Agent, no resuelve CAPTCHAs, no usa endpoints privados.
- Concurrencia acotada (por defecto 5, máximo 10).
- Ante 429 espera según Retry-After con tope; si se supera, ese sitio queda como no concluyente.
- Si la URL final ya no contiene el usuario (redirección al inicio o al login), no cuenta como hallazgo.

Confianza: heurística por criterio (estado 0.6, texto 0.75), salvo que la entrada de sites.json
defina "confianza". Los criterios de sites.json NO están verificados en vivo.
"""

import asyncio
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

from aleph.connectors._util import (
    HttpSession,
    RateLimited,
    clean_handle,
    positive_int,
    require_text,
    utcnow,
)
from aleph.connectors.base import Connector, ConnectorError, register
from aleph.core.schemas import CollectionResult, EntityRecord

SITES_PATH = Path(__file__).with_name("sites.json")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
CONFIDENCE_BY_CRITERION = {"status": 0.6, "text": 0.75}
MAX_BODY_CHARS = 300_000  # tope de texto que se revisa por sitio
DEFAULT_CONCURRENCY = 5
MAX_CONCURRENCY = 10


@lru_cache(maxsize=1)
def load_sites() -> tuple[dict[str, Any], ...]:
    """Carga y valida sites.json. Se lee una sola vez por proceso."""
    try:
        data = json.loads(SITES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConnectorError(f"no se pudo leer el catálogo de sitios: {exc}") from exc
    sites = data.get("sitios") if isinstance(data, dict) else None
    if not isinstance(sites, list) or not sites:
        raise ConnectorError("catálogo de sitios vacío o mal formado")
    for site in sites:
        check = site.get("check") or {}
        if not all(site.get(k) for k in ("id", "name", "url")) or "{username}" not in site["url"]:
            raise ConnectorError(f"entrada de sitio inválida: {site.get('id', '?')}")
        if check.get("type") not in ("status", "text"):
            raise ConnectorError(f"criterio de detección inválido en {site['id']}")
    return tuple(sites)


def _decide(check: dict[str, Any], status: int, body: str) -> str:
    """'hit' si la cuenta existe, 'miss' si no existe, 'unknown' si la respuesta no alcanza."""
    if check["type"] == "status":
        if status == check.get("found", 200):
            return "hit"
        if status == check.get("missing", 404):
            return "miss"
        return "unknown"
    if status == 404:
        return "miss"
    if status != 200:
        return "unknown"
    if check.get("missing_text") and check["missing_text"] in body:
        return "miss"
    if check.get("found_text"):
        return "hit" if check["found_text"] in body else "miss"
    return "hit"


@register
class UsernameSearchConnector(Connector):
    name = "username_search"
    title = "Búsqueda de nombre de usuario (sitios públicos curados)"
    mode = "live"
    params: ClassVar[dict[str, str]] = {
        "handle": "Nombre de usuario a buscar (letras, números, punto, guion y guion bajo)",
        "limit": "Máximo de sitios a consultar, en el orden de sites.json (por defecto, todos)",
        "concurrency": "Consultas simultáneas (por defecto 5, máximo 10)",
    }

    async def collect(
        self, *, handle: str, limit: int | None = None, concurrency: int | None = None
    ) -> CollectionResult:
        username = clean_handle(require_text(handle, "handle"))
        if not USERNAME_RE.match(username):
            raise ConnectorError(f"nombre de usuario inválido: {username!r}")
        sites = list(load_sites())
        sites = sites[: positive_int(limit, "limit", len(sites))]
        concurrency = positive_int(concurrency, "concurrency", DEFAULT_CONCURRENCY, MAX_CONCURRENCY)
        semaphore = asyncio.Semaphore(concurrency)

        async with HttpSession(
            self.client, timeout=10.0, max_retries=1, max_retry_after=10.0
        ) as http:
            outcomes = await asyncio.gather(
                *(self._check(http, site, username, semaphore) for site in sites)
            )

        by_id = {site["id"]: site for site in sites}
        entities: list[EntityRecord] = []
        for outcome in outcomes:
            if outcome["outcome"] != "hit":
                continue
            site = by_id[outcome["site"]]
            criterion = site["check"]["type"]
            confidence = float(site.get("confianza", CONFIDENCE_BY_CRITERION[criterion]))
            entities.append(
                EntityRecord(
                    type="account",
                    label=username,
                    props={
                        "platform": site["name"],
                        "url": outcome["url"],
                        "criterion": criterion,
                        "http_status": outcome["status"],
                        "site_id": site["id"],
                    },
                    confidence=confidence,
                    ref=f"account:{site['id']}:{username.lower()}",
                )
            )

        warnings: list[str] = []
        counts = Counter(outcome["outcome"] for outcome in outcomes)
        inconclusive = counts["unknown"] + counts["error"] + counts["rate_limited"]
        if inconclusive:
            warnings.append(
                f"{inconclusive} de {len(sites)} sitios no concluyentes "
                f"(sin respuesta clara: {counts['unknown']}, errores: {counts['error']}, "
                f"límite de tasa: {counts['rate_limited']})"
            )
        if counts["rate_limited"]:
            warnings.append(
                f"límite de tasa en {counts['rate_limited']} sitios: esperar más de lo permitido; "
                "se devuelven sólo los hallazgos obtenidos."
            )

        return CollectionResult(
            connector=self.name,
            reference=f"username:{username}",
            retrieved_at=utcnow(),
            entities=entities,
            warnings=warnings,
            raw=list(outcomes),
        )

    async def _check(
        self,
        http: HttpSession,
        site: dict[str, Any],
        username: str,
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        url = site["url"].replace("{username}", quote(username, safe=""))
        outcome: dict[str, Any] = {
            "site": site["id"],
            "name": site["name"],
            "url": url,
            "status": None,
            "outcome": "unknown",
            "reason": "",
        }
        async with semaphore:
            try:
                response = await http.request("GET", url)
            except RateLimited:
                outcome["outcome"] = "rate_limited"
                return outcome
            except ConnectorError as exc:
                outcome["outcome"] = "error"
                outcome["reason"] = str(exc)
                return outcome

        outcome["status"] = response.status_code
        body = response.text[:MAX_BODY_CHARS] if site["check"]["type"] == "text" else ""
        decision = _decide(site["check"], response.status_code, body)
        if decision == "hit" and username.lower() not in str(response.url).lower():
            decision = "unknown"
            outcome["reason"] = "la URL final no contiene el usuario (posible redirección)"
        outcome["outcome"] = decision
        return outcome
