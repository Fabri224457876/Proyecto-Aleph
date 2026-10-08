"""Enriquecimiento de IOCs: proveedores con interfaz común y orquestador.

Uso típico:

    report = await enrich_indicator(entity)            # todos los proveedores que aplican
    report.verdict, report.entities, report.relations  # resumen combinado
"""

from .base import EnrichmentProvider, EnrichmentResult, Status, Verdict
from .crtsh import CrtSh
from .hibp import Hibp
from .malwarebazaar import MalwareBazaar
from .orchestrator import DEFAULT_PROVIDERS, EnrichmentReport, enrich_indicator
from .otx import Otx
from .rdap import Rdap
from .shodan_internetdb import ShodanInternetDB
from .threatfox import ThreatFox
from .urlhaus import URLhaus
from .virustotal import VirusTotal

__all__ = [
    "DEFAULT_PROVIDERS",
    "CrtSh",
    "EnrichmentProvider",
    "EnrichmentReport",
    "EnrichmentResult",
    "Hibp",
    "MalwareBazaar",
    "Otx",
    "Rdap",
    "ShodanInternetDB",
    "Status",
    "ThreatFox",
    "URLhaus",
    "Verdict",
    "VirusTotal",
    "enrich_indicator",
]
