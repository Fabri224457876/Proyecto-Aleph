"""Historia ficticia del caso de demostración. Todo el texto es inventado.

Los dominios son *.example (reservados), las IPs son de documentación (RFC 5737), la billetera de
Bitcoin es el vector de prueba de BIP-173, el ETH es una cadena inventada y los hashes salen de
frases de ejemplo. Las cuentas y publicaciones las genera aleph.menard.synth. Nada de esto apunta a
personas, empresas, sistemas o lugares de un hecho real: los lugares son solo coordenadas públicas
para que el motor de contradicciones tenga algo que medir.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone

DEMO_CASE_NAME = "[FICTICIO] Campaña de phishing «Cupón Austral» (demostración)"
DEMO_DESCRIPTION = (
    "Escenario de demostración con datos 100 % ficticios. La campaña, la entidad imitada, los dominios "
    "(*.example), las IPs de documentación, las billeteras y los hashes son de ejemplo; las cuentas y "
    "publicaciones son sintéticas (aleph.menard.synth). No corresponde a personas, empresas ni hechos "
    "reales. Sirve para recorrer el flujo completo: recolección, grafo, MENARD con revisión humana, "
    "extracción de IOCs, FUNES y exportación STIX."
)
DEMO_LEGAL_BASIS = (
    "Demostración con datos sintéticos: no hay personas reales ni investigación en curso. "
    "No se procesan datos personales reales (Ley 25.326)."
)
DEMO_CONNECTOR = "aleph-sintetico"
DEMO_PERSONAS = 16
DEMO_SEED = 7
DEMO_RETRIEVED_AT = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
DEMO_ANALYST = "analista-demo"
DEMO_AUDITOR = "auditor-demo"
DEMO_ADMIN = "admin"

ART = timezone(timedelta(hours=-3))
CAMPAIGN = "Campaña «Cupón Austral»"
OPERATOR = "Operador ficticio A"
ORG = "Banco Austral Ficticio"
MALWARE = "Troyano NubeGris"
BTC = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
ETH = "0xd8c2f1a4e9b7c3d5a6f0e1b2c3d4e5f607182930"
DROPPER_SHA256 = hashlib.sha256(b"aleph-demo: instalador ficticio NubeGris v1").hexdigest()
PHISH_URL = "https://pagos-seguros.example/login"

# (ref, tipo, etiqueta, props, confianza). Confirmadas: las cargó el analista del caso.
INFRA_ENTITIES: list[tuple[str, str, str, dict, float]] = [
    ("camp", "event", CAMPAIGN, {
        "date": "2026-03-01",
        "description": "Campaña de phishing inventada: un cupón de descuento falso como gancho.",
    }, 1.0),
    ("org", "organization", ORG, {"description": "Entidad inventada: la marca que imita la campaña."}, 1.0),
    ("mal", "malware", MALWARE, {"is_family": True, "description": "Troyano ficticio para la demostración."}, 1.0),
    ("d1", "domain", "pagos-seguros.example", {
        "malicious": True, "description": "Página de phishing ficticia.",
    }, 0.9),
    ("d2", "domain", "verificacion-cuenta.example", {
        "malicious": True, "description": "Captura de credenciales (ficticia).",
    }, 0.9),
    ("d3", "domain", "cdn-cupones.example", {
        "malicious": False, "description": "Servidor de descarga del troyano (ficticio).",
    }, 0.8),
    ("u1", "url", PHISH_URL, {"malicious": True, "value": PHISH_URL}, 0.9),
    ("ip1", "ip", "192.0.2.44", {"scope": "documentation", "routable": False, "version": 4}, 1.0),
    ("ip2", "ip", "198.51.100.23", {"scope": "documentation", "routable": False, "version": 4}, 1.0),
    ("h1", "hash", DROPPER_SHA256, {
        "algorithm": "SHA-256", "value": DROPPER_SHA256, "malicious": True,
        "description": "Hash de un instalador ficticio.",
    }, 0.9),
    ("w1", "wallet", BTC, {
        "currency": "BTC", "address": BTC, "format": "p2wpkh",
        "description": "Vector de prueba BIP-173: dirección de ejemplo.",
    }, 0.9),
    ("loc1", "location", "Rosario", {"country": "AR", "city": "Rosario",
                                     "latitude": -32.9442, "longitude": -60.6505}, 1.0),
    ("loc2", "location", "Ushuaia", {"country": "AR", "city": "Ushuaia",
                                     "latitude": -54.8019, "longitude": -68.3030}, 1.0),
]
# (origen, destino, tipo, props, confianza)
INFRA_RELATIONS: list[tuple[str, str, str, dict, float]] = [
    ("camp", "mal", "uses", {"description": "El cupón instala el troyano."}, 0.9),
    ("camp", "org", "targets", {}, 0.9),
    ("camp", "loc1", "located_at", {}, 0.7),
    ("mal", "d3", "communicates_with", {}, 0.8),
    ("h1", "mal", "indicates", {}, 0.8),
    ("d1", "ip1", "resolves_to", {}, 0.8),
    ("d2", "ip2", "resolves_to", {}, 0.7),
    ("u1", "d1", "related_to", {}, 0.9),
]
# Propuestas que quedan para revisión humana (no confirmadas)
PENDING_ENTITIES: list[tuple[str, str, str, dict, float]] = [
    ("op", "person", OPERATOR, {
        "description": "Persona inventada. Atribuirle las cuentas es una hipótesis, no un hecho.",
    }, 0.6),
    ("ethw", "wallet", ETH, {"currency": "ETH", "address": ETH}, 0.8),
]
PENDING_RELATIONS: list[tuple[str, str, str, dict, float]] = [
    ("op", "camp", "associated_with", {"description": "Hipótesis a revisar."}, 0.5),
    ("ethw", "camp", "related_to", {"description": "Hipótesis: la billetera recibe pagos de la campaña."}, 0.5),
]
# Texto de la campaña: el motor de reglas de FUNES lo analiza (sin LLM)
CAMPAIGN_MESSAGE = (
    "Tu cupón de descuento vence hoy. Ingresá en " + PHISH_URL + " o escribí a "
    "soporte@verificacion-cuenta.example para validar tus datos. Pagá la reserva con la billetera " + BTC + "."
)
# Denuncia ficticia de un foro: el extractor de IOCs la procesa (la misma ruta que POST /ioc/extract)
COMMUNITY_REPORT = (
    "Denuncia ficticia en un foro de la comunidad: el archivo cupon-austral.zip tiene SHA-256 "
    + DROPPER_SHA256 + ". Los pagos llegan a la billetera " + ETH + " y el sitio pagos-seguros[.]example "
    "figura en el mensaje. Recomendamos no abrir el archivo."
)
CONFIRMED_TECHNIQUES: list[tuple[str, float, str]] = [
    ("T1566.002", 85.0, "El mensaje lleva enlaces a una página que imita a la entidad (ficticio)."),
    ("T1204.002", 70.0, "El troyano llega como archivo comprimido que la víctima debe abrir (ficticio)."),
    ("T1583.001", 60.0, "Se registraron dominios de ejemplo parecidos a la marca (ficticio)."),
]
PROPOSED_TECHNIQUES: list[tuple[str, float, str]] = [
    ("T1102", 40.0, "Posible uso de un servicio web para el control del troyano: a confirmar."),
]
SECTION_UNCLASSIFIED = "Sin clasificar"
SECTION_ACTIVITY = "Actividad"


def contradiction_claims():
    """Dos afirmaciones del mismo sujeto con 20 minutos de diferencia y unos 2 500 km de distancia."""
    from aleph.funes.contradictions import Claim, Place

    return [
        Claim(
            id="c-rosario", subject=OPERATOR, predicate="visto_en", value="Rosario",
            start=datetime(2026, 3, 12, 14, 0, tzinfo=ART), time_mode="durante",
            place=Place(name="Rosario", lat=-32.9442, lon=-60.6505),
            source="Registro de acceso (ficticio)", quote="12/03/2026 14:00: acceso de Rosario, registro inventado.",
            confidence=0.9,
        ),
        Claim(
            id="c-ushuaia", subject=OPERATOR, predicate="visto_en", value="Ushuaia",
            start=datetime(2026, 3, 12, 14, 20, tzinfo=ART), time_mode="durante",
            place=Place(name="Ushuaia", lat=-54.8019, lon=-68.3030),
            source="Publicación en red social (ficticia)",
            quote="12/03/2026 14:20: etiqueta de Ushuaia en una publicación inventada.", confidence=0.8,
        ),
    ]


def findings_plan(accounts: list[tuple[str, str]]) -> list[dict]:
    """Hallazgos del expediente, por inciso. `accounts`: [(plataforma, handle)] del operador ficticio."""
    plan: list[dict] = [
        {"section": "Identidad", "kind": "text",
         "value": f"Se presenta como «{OPERATOR}» y dice vivir en Rosario (dato inventado).",
         "quote": f"Se presenta como «{OPERATOR}», con biografía de «Rosario» (dato inventado para la demo).",
         "page_url": "https://foro-ficticio.example/hilo/1", "note": ""},
    ]
    for platform, handle in accounts[:2]:
        plan.append({
            "section": "Cuentas", "kind": "account", "value": f"@{handle}", "platform": platform,
            "quote": f"Publicó el cupón desde la cuenta @{handle} (captura ficticia).",
            "page_url": f"https://red-social-ficticia.example/{platform}/{handle}", "note": "",
        })
    plan += [
        {"section": "Contactos", "kind": "text",
         "value": "Correo de soporte del mensaje: soporte@verificacion-cuenta.example (ficticio).",
         "quote": "Escribí a soporte@verificacion-cuenta.example para validar tus datos.",
         "page_url": "https://foro-ficticio.example/hilo/2", "note": "Correo de un dominio de ejemplo."},
        {"section": "Ubicaciones", "kind": "location", "value": "Rosario",
         "quote": "12/03/2026 14:00: acceso de Rosario (registro inventado).",
         "page_url": "https://registro-ficticio.example/acceso/2026-03-12", "note": ""},
        {"section": "Ubicaciones", "kind": "location", "value": "Ushuaia",
         "quote": "12/03/2026 14:20: etiqueta de Ushuaia en una publicación inventada.",
         "page_url": "https://red-social-ficticia.example/publicacion/9", "note": ""},
        {"section": SECTION_ACTIVITY, "kind": "text",
         "value": "El cupón se difundió el 1 de marzo de 2026 con tres enlaces de pago.",
         "quote": "El cupón se difundió el 1 de marzo de 2026 con tres enlaces de pago (ficticio).",
         "page_url": "https://foro-ficticio.example/hilo/3", "note": ""},
        {"section": "Infraestructura", "kind": "domain", "value": "pagos-seguros.example",
         "quote": "pagos-seguros.example aparece como destino del botón «Ver cupón» (ficticio).",
         "page_url": "https://foro-ficticio.example/hilo/4", "note": ""},
        {"section": "Infraestructura", "kind": "hash", "value": DROPPER_SHA256,
         "quote": "El instalador tiene el SHA-256 " + DROPPER_SHA256 + " (ficticio).",
         "page_url": "https://foro-ficticio.example/hilo/5", "note": ""},
        {"section": "Infraestructura", "kind": "wallet", "value": BTC,
         "quote": "Pagá la reserva con la billetera " + BTC + " (ficticio).",
         "page_url": "https://foro-ficticio.example/hilo/6", "note": ""},
        {"section": SECTION_UNCLASSIFIED, "kind": "text",
         "value": "Pendiente: confirmar si 198.51.100.23 es del mismo proveedor de hosting.",
         "quote": "Pendiente: confirmar si 198.51.100.23 es del mismo proveedor de hosting.",
         "page_url": "https://notas-ficticias.example/caso/1", "note": "Nota de trabajo del analista."},
    ]
    return plan
