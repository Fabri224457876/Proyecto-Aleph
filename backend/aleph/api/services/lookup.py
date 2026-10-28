"""Búsqueda OSINT externa: reconoce el dato y arma el plan con los conectores y herramientas que aplican.

No ejecuta nada: devuelve acciones listas para correr con `POST /cases/{id}/collect` o
`POST /cases/{id}/tools/{name}/run`, así cada una queda como trabajo, con su fuente y en la
auditoría, igual que si se corriera a mano. Los conectores que necesitan una clave que no está
configurada se informan como no disponibles en lugar de esconderlos.
"""

import ipaddress
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["url", "domain", "email", "ip", "hash", "btc", "eth", "cve", "phone", "handle", "text", "empty"]

LABELS: dict[str, str] = {
    "empty": "nada", "url": "una URL", "domain": "un dominio", "email": "un correo electrónico",
    "ip": "una dirección IP", "hash": "un hash", "btc": "una billetera de Bitcoin",
    "eth": "una billetera de Ethereum", "cve": "una vulnerabilidad (CVE)", "phone": "un número de teléfono",
    "handle": "un nombre de usuario", "text": "un texto",
}

_RE = {
    "url": re.compile(r"^https?://\S+$", re.IGNORECASE),
    "email": re.compile(r"^[^\s@]+@[^\s@]+\.[a-z]{2,}$", re.IGNORECASE),
    "hash": re.compile(r"^([a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64})$", re.IGNORECASE),
    "btc": re.compile(r"^(bc1[a-z0-9]{25,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$"),
    "eth": re.compile(r"^0x[a-fA-F0-9]{40}$"),
    "cve": re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE),
    "domain": re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", re.IGNORECASE),
    "phone": re.compile(r"^\+?[\d\s()-]+$"),
    "handle": re.compile(r"^@?[A-Za-z0-9_.-]{2,40}$"),
}


def detect(raw: str) -> tuple[str, str]:
    """(tipo, valor normalizado). Mismo criterio que la consola simple (`detect.ts`)."""
    value = raw.strip()
    if not value:
        return "empty", value
    if "\n" in value or len(value) > 120 or re.search(r"\s", value):
        if not re.search(r"\s", value) and _RE["url"].match(value):
            return "url", value
        return "text", value
    if _RE["url"].match(value):
        return "url", value
    if _RE["email"].match(value):
        return "email", value.lower()
    try:
        if ipaddress.ip_address(value).version == 4 and value.count(".") == 3:
            return "ip", value
    except ValueError:
        pass
    if _RE["cve"].match(value):
        return "cve", value.upper()
    if _RE["hash"].match(value):
        return "hash", value.lower()
    if _RE["eth"].match(value):
        return "eth", value
    if _RE["btc"].match(value):
        return "btc", value
    if _RE["phone"].match(value) and len(re.sub(r"\D", "", value)) >= 7:
        return "phone", value
    if _RE["domain"].match(value):
        return "domain", value.lower()
    if _RE["handle"].match(value):
        return "handle", value.lstrip("@")
    return "text", value


class Action(BaseModel):
    id: str
    title: str
    description: str
    kind: Literal["connector", "tool"]
    name: str
    method: str = "POST"
    path: str = Field(description="Ruta relativa a /api, ya con el caso")
    form: dict[str, Any] = Field(description="Campos del formulario a enviar")
    network: bool = True
    available: bool = True
    unavailable_reason: str = ""


class LookupPlan(BaseModel):
    query: str
    kind: str
    kind_label: str
    value: str
    actions: list[Action]
    note: str = ""


def _connector(case_id: int, name: str, title: str, description: str, params: dict[str, Any]) -> Action:
    import json

    return Action(id=f"connector:{name}", title=title, description=description, kind="connector", name=name,
                  path=f"/cases/{case_id}/collect", form={"connector": name, "params": json.dumps(params)})


def _tool(case_id: int, name: str, title: str, description: str, target: str, network: bool = True) -> Action:
    return Action(id=f"tool:{name}", title=title, description=description, kind="tool", name=name,
                  path=f"/cases/{case_id}/tools/{name}/run", form={"target": target}, network=network)


def plan(case_id: int, query: str, settings) -> LookupPlan:
    kind, value = detect(query)
    actions: list[Action] = []
    c, t = (lambda *a: actions.append(_connector(case_id, *a))), (lambda *a, **k: actions.append(_tool(case_id, *a, **k)))
    if kind == "handle":
        c("username_search", "El usuario en cientos de sitios", "Comprueba en qué sitios públicos existe ese nombre de usuario.",
          {"handle": value})
        c("bluesky", "Perfil en Bluesky", "Perfil, publicaciones y red pública en Bluesky.",
          {"handle": value if "." in value else f"{value}.bsky.social"})
        c("github", "Perfil en GitHub", "Perfil, repositorios y actividad pública.", {"handle": value})
        c("reddit", "Usuario de Reddit", "Perfil y comentarios públicos.", {"handle": value})
        c("telegram_channel", "Canal público de Telegram", "Mensajes del canal público con ese nombre, si existe.",
          {"handle": value})
        x = _connector(case_id, "x_api", "Cuenta de X (API oficial)", "Perfil y publicaciones por la API oficial.",
                       {"handle": value})
        if not (settings.x_bearer_token or "").strip():
            x.available, x.unavailable_reason = False, "Falta la clave ALEPH_X_BEARER_TOKEN."
        actions.append(x)
        t("dorks", "Búsquedas avanzadas sugeridas", "Arma consultas de buscador para revisar a mano.", value, network=False)
    elif kind == "email":
        t("email_intel", "Correo electrónico", "Dominio, registros MX y, con clave, filtraciones conocidas.", value)
        t("dorks", "Búsquedas avanzadas sugeridas", "Consultas de buscador para revisar a mano.", value, network=False)
    elif kind == "domain":
        c("domain_intel", "Inteligencia del dominio", "DNS, certificados y subdominios públicos.", {"domain": value})
        t("wayback", "Historial en Wayback Machine", "Capturas archivadas del sitio.", value)
        t("typosquat", "Dominios parecidos (typosquatting)", "Variantes registradas que imitan al dominio.", value)
        t("favicon", "Huella del favicon", "Hash del ícono para encontrar sitios hermanos.", value)
        t("dorks", "Búsquedas avanzadas sugeridas", "Consultas de buscador para revisar a mano.", value, network=False)
    elif kind == "url":
        t("wayback", "Historial en Wayback Machine", "Capturas archivadas de esa dirección.", value)
        t("favicon", "Huella del favicon", "Hash del ícono del sitio.", value)
    elif kind == "ip":
        t("asn", "Red y ASN", "A qué red y organización pertenece la IP.", value)
    elif kind in ("btc", "eth"):
        t("wallet", "Billetera", "Saldo y movimientos públicos en la cadena.", value)
    elif kind == "cve":
        t("vulns", "Vulnerabilidad", "Detalle, gravedad y si figura en KEV (explotada activamente).", value)
    elif kind == "phone":
        t("phone_intel", "Número de teléfono", "País, tipo de línea y formato (sin consultas externas).", value,
          network=False)
    note = {
        "hash": "Para un hash no hay fuentes públicas configuradas: buscalo dentro de Aleph.",
        "text": "Es un texto: pegalo en Reunir para que FUNES extraiga los datos, o buscalo dentro de Aleph.",
        "empty": "Escribí un usuario, correo, dominio, URL, IP, billetera, CVE o teléfono.",
    }.get(kind, "Cada acción consulta fuentes públicas en solo lectura y queda auditada.")
    return LookupPlan(query=query, kind=kind, kind_label=LABELS[kind], value=value, actions=actions, note=note)
