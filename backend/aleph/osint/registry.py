"""Registro de la caja de herramientas OSINT: nombre, título, descripción, entradas aceptadas y clave.

Todas las herramientas son de solo lectura. Ninguna requiere clave para funcionar; algunas admiten una
clave opcional para elevar su límite de consultas (ver el campo `key_settings`).
`entrypoint` apunta a la función pública del módulo (módulo:función).
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable

from pydantic import BaseModel


class ToolSpec(BaseModel):
    name: str
    title: str
    description: str
    inputs: tuple[str, ...]  # tipos de entrada que acepta
    network: bool  # consulta servicios externos (siempre en solo lectura)
    requires_key: bool  # si no hay clave, la herramienta no funciona
    key_settings: tuple[str, ...] = ()  # campos de core/config.py que podría usar como clave opcional
    entrypoint: str
    is_async: bool


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="image_meta",
        title="Metadatos de imagen",
        description=("EXIF de la imagen (cámara, software, fecha y ubicación GPS en decimal), SHA-256 y "
                     "hash perceptual. Incluye enlaces de búsqueda inversa para una URL de imagen."),
        inputs=("imagen", "URL de imagen"), network=False, requires_key=False,
        entrypoint="aleph.osint.image_meta:analyze_image", is_async=False,
    ),
    ToolSpec(
        name="doc_meta",
        title="Metadatos de documentos",
        description=("Autor, último editor, software, fechas y empresa de un PDF (diccionario Info y XMP) "
                     "o de un documento Office (docx, xlsx, pptx). Parseo defensivo: límites y sin entidades XML."),
        inputs=("archivo PDF", "archivo Office Open XML"), network=False, requires_key=False,
        entrypoint="aleph.osint.doc_meta:analyze_document", is_async=False,
    ),
    ToolSpec(
        name="wayback",
        title="Historial en Internet Archive",
        description=("Capturas de una URL o dominio (API CDX), primera y última captura, cambios de contenido "
                     "por digest y captura más cercana a una fecha (API de disponibilidad)."),
        inputs=("URL", "dominio"), network=True, requires_key=False,
        entrypoint="aleph.osint.wayback:analyze_url", is_async=True,
    ),
    ToolSpec(
        name="dorks",
        title="Consultas de buscador",
        description=("Genera consultas categorizadas para Google, Bing y DuckDuckGo sobre un dominio, nombre, "
                     "usuario o email, con sus URL de búsqueda. Solo genera; no ejecuta búsquedas."),
        inputs=("dominio", "nombre", "nombre de usuario", "email"), network=False, requires_key=False,
        entrypoint="aleph.osint.dorks:build_dorks", is_async=False,
    ),
    ToolSpec(
        name="typosquat",
        title="Dominios parecidos",
        description=("Variantes de un dominio (omisión, transposición, tecla vecina, homoglifos, TLD, guiones, "
                     "subdominio, bitsquatting) y cuáles resuelven por DNS. No visita los sitios."),
        inputs=("dominio",), network=True, requires_key=False,
        entrypoint="aleph.osint.typosquat:analyze_typosquat", is_async=True,
    ),
    ToolSpec(
        name="favicon",
        title="Hash de favicon (estilo Shodan)",
        description=("Descarga el favicon de un sitio y calcula su hash MurmurHash3 de 32 bits sobre el base64, "
                     "con la consulta http.favicon.hash de Shodan, que solo se construye."),
        inputs=("dominio", "URL"), network=True, requires_key=False,
        entrypoint="aleph.osint.favicon:analyze_favicon", is_async=True,
    ),
    ToolSpec(
        name="asn",
        title="IP a ASN y prefijos",
        description=("ASN, prefijo, organización titular y país de registro de una IP pública (RIPEstat), "
                     "y prefijos anunciados por un ASN."),
        inputs=("IP", "ASN"), network=True, requires_key=False,
        entrypoint="aleph.osint.asn:lookup_ip", is_async=True,
    ),
    ToolSpec(
        name="wallet",
        title="Wallets de Bitcoin y Ethereum",
        description=("Valida direcciones (Base58Check, Bech32/Bech32m, EIP-55 con Keccak-256). En Bitcoin "
                     "mainnet consulta saldo y transacciones recientes en Blockstream."),
        inputs=("wallet",), network=True, requires_key=False,
        entrypoint="aleph.osint.wallet:analyze_wallet", is_async=True,
    ),
    ToolSpec(
        name="vulns",
        title="Vulnerabilidades (CVE)",
        description=("Descripción y CVSS de un CVE desde NVD (API 2.0) y si figura en el catálogo KEV de CISA, "
                     "con la fecha en que se incluyó."),
        inputs=("CVE",), network=True, requires_key=False,
        entrypoint="aleph.osint.vulns:lookup_cve", is_async=True,
    ),
    ToolSpec(
        name="ransomware",
        title="Víctimas de ransomware",
        description=("Víctimas publicadas por grupos de ransomware (ransomware.live v2): recientes, por país "
                     "(código ISO-2, p. ej. AR) o por grupo. Son reivindicaciones de los grupos."),
        inputs=("país ISO-2", "grupo"), network=True, requires_key=False,
        entrypoint="aleph.osint.ransomware:list_victims", is_async=True,
    ),
    ToolSpec(
        name="evidence",
        title="Preservación de evidencia",
        description=("Guarda contenido con su URL de origen, SHA-256, manifiesto JSON con sello UTC y cadena "
                     "de custodia encadenada, y verifica que no fue alterado. Sin red."),
        inputs=("contenido (bytes)", "URL de origen"), network=False, requires_key=False,
        entrypoint="aleph.osint.evidence:preserve_evidence", is_async=False,
    ),
    ToolSpec(
        name="email_intel",
        title="Análisis de email sin contacto",
        description=("Sintaxis, registros MX, SPF y DMARC del dominio, dominio descartable y URL de Gravatar. "
                     "No verifica si la cuenta existe ni contacta al titular."),
        inputs=("email",), network=True, requires_key=False,
        entrypoint="aleph.osint.email_intel:analyze_email", is_async=True,
    ),
    ToolSpec(
        name="phone_intel",
        title="Análisis de teléfono",
        description=("Normaliza a E.164, indica país y, para Argentina, tipo de línea y área por característica. "
                     "Solo analiza el número; no consulta servicios de terceros."),
        inputs=("teléfono",), network=False, requires_key=False,
        entrypoint="aleph.osint.phone_intel:analyze_phone", is_async=False,
    ),
)


def list_tools() -> list[ToolSpec]:
    return list(TOOLS)


def get_tool(name: str) -> ToolSpec:
    for tool in TOOLS:
        if tool.name == name:
            return tool
    raise KeyError(f"herramienta desconocida: {name}")


def resolve_entrypoint(tool: ToolSpec) -> Callable:
    """Importa y devuelve la función pública de la herramienta."""
    module_name, _, func_name = tool.entrypoint.partition(":")
    return getattr(importlib.import_module(module_name), func_name)


def is_async_entrypoint(tool: ToolSpec) -> bool:
    return inspect.iscoroutinefunction(resolve_entrypoint(tool))
