"""Generador de consultas de buscador («dorks») para un objetivo: dominio, nombre, usuario o email.

Solo construye las consultas y las URL de búsqueda en Google, Bing y DuckDuckGo. No ejecuta
búsquedas ni consulta esos buscadores.

Alcance deliberado: no se generan consultas para credenciales, claves de API, archivos de
configuración con secretos ni volcados de bases de datos. Las categorías cubren la superficie pública
de un objetivo: documentos publicados, perfiles, menciones, subdominios indexados y paneles de acceso
visibles. Los operadores siguen la sintaxis pública documentada de cada buscador.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import quote_plus

from pydantic import BaseModel, Field

from .errors import InvalidInputError

TargetType = Literal["auto", "domain", "name", "username", "email"]
MAX_TARGET_LEN = 254

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,63}")
_DOMAIN_RE = re.compile(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_USERNAME_RE = re.compile(r"@?[A-Za-z0-9._-]{3,32}")


class DorkQuery(BaseModel):
    category: str
    description: str
    query: str
    search_urls: dict[str, str]  # motor → URL de búsqueda (no se consulta)


class DorkReport(BaseModel):
    target: str
    target_type: str
    queries: list[DorkQuery] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _search_urls(query: str) -> dict[str, str]:
    encoded = quote_plus(query)
    return {
        "google": f"https://www.google.com/search?q={encoded}",
        "bing": f"https://www.bing.com/search?q={encoded}",
        "duckduckgo": f"https://duckduckgo.com/?q={encoded}",
    }


def detect_target_type(target: str) -> str:
    """Clasifica el objetivo en email, domain, name o username. Lanza InvalidInputError si no encaja."""
    value = (target or "").strip()
    if not value or len(value) > MAX_TARGET_LEN:
        raise InvalidInputError("objetivo vacío o demasiado largo")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value if ch != " "):
        raise InvalidInputError("objetivo con caracteres de control")
    collapsed = " ".join(value.split())
    if " " in collapsed:
        return "name"
    if _EMAIL_RE.fullmatch(collapsed):
        return "email"
    if _DOMAIN_RE.fullmatch(collapsed.lower()):
        return "domain"
    if _USERNAME_RE.fullmatch(collapsed):
        return "username"
    raise InvalidInputError("no se reconoce el tipo de objetivo (dominio, nombre, usuario o email)")


def _clean(value: str) -> str:
    return value.replace('"', "").strip()


def _domain_queries(d: str) -> list[tuple[str, str, str]]:
    return [
        ("subdominios", "Subdominios indexados por buscadores", f"site:*.{d} -site:www.{d}"),
        ("documentos", "Documentos publicados en el dominio",
         (f"site:{d} (filetype:pdf OR filetype:doc OR filetype:docx OR filetype:xls OR filetype:xlsx "
          "OR filetype:ppt OR filetype:pptx)")),
        ("directorios", "Listados de directorio indexados", f'site:{d} intitle:"index of"'),
        ("accesos", "Páginas de inicio de sesión visibles", f"site:{d} (inurl:login OR inurl:signin OR inurl:admin)"),
        ("menciones", "Menciones del dominio fuera de su sitio", f'"{d}" -site:{d}'),
        ("codigo", "Repositorios públicos que mencionan el dominio", f'site:github.com "{d}"'),
    ]


def _name_queries(n: str) -> list[tuple[str, str, str]]:
    return [
        ("perfiles", "Perfiles profesionales", f'site:linkedin.com/in "{n}"'),
        ("documentos", "Documentos que contienen el nombre", f'"{n}" (filetype:pdf OR filetype:doc OR filetype:docx)'),
        ("redes", "Menciones en redes sociales",
         f'"{n}" (site:x.com OR site:instagram.com OR site:facebook.com OR site:bsky.app)'),
        ("menciones", "Menciones generales", f'"{n}"'),
    ]


def _username_queries(u: str) -> list[tuple[str, str, str]]:
    return [
        ("perfiles", "Perfiles en plataformas públicas",
         f'"{u}" (site:github.com OR site:reddit.com OR site:x.com OR site:instagram.com OR site:bsky.app)'),
        ("menciones", "Menciones exactas del usuario", f'"{u}"'),
        ("foros", "Perfiles en foros y comunidades", f'"{u}" (inurl:profile OR inurl:user OR inurl:members)'),
    ]


def _email_queries(e: str) -> list[tuple[str, str, str]]:
    return [
        ("menciones", "Menciones exactas de la dirección", f'"{e}"'),
        ("documentos", "Documentos que contienen la dirección", f'"{e}" (filetype:pdf OR filetype:doc OR filetype:xls)'),
        ("perfiles", "Perfiles asociados a la dirección", f'"{e}" (site:github.com OR site:linkedin.com)'),
    ]


def build_dorks(target: str, *, target_type: TargetType = "auto") -> DorkReport:
    """Genera consultas categorizadas con sus URL de búsqueda. Función sync: solo construye texto."""
    value = (target or "").strip()
    detected = detect_target_type(value) if target_type == "auto" else target_type
    if detected == "domain":
        value = value.lower()
        if not _DOMAIN_RE.fullmatch(value):
            raise InvalidInputError("dominio inválido")
        raw_queries = _domain_queries(value)
    elif detected == "name":
        cleaned = " ".join(_clean(value).split())
        if not cleaned:
            raise InvalidInputError("nombre vacío")
        raw_queries = _name_queries(cleaned)
    elif detected == "username":
        raw_queries = _username_queries(_clean(value).lstrip("@"))
    elif detected == "email":
        if not _EMAIL_RE.fullmatch(value):
            raise InvalidInputError("email inválido")
        raw_queries = _email_queries(value.lower())
    else:
        raise InvalidInputError("tipo de objetivo no soportado")

    queries = [
        DorkQuery(category=category, description=description, query=query, search_urls=_search_urls(query))
        for category, description, query in raw_queries
    ]
    return DorkReport(target=value, target_type=detected, queries=queries)
