"""Vulnerabilidades (CVE): descripción y CVSS desde NVD, y si figura en el catálogo KEV de CISA.

Documentación oficial en la que se basa:
- NVD, CVE API 2.0: https://nvd.nist.gov/developers/vulnerabilities
  Endpoint https://services.nvd.nist.gov/rest/json/cves/2.0 con el parámetro cveId. La clave opcional va en
  la cabecera apiKey. Sin clave el límite de consultas es bajo. Según el aviso técnico de NVD de mayo de 2025,
  el límite pasó de 403 a 429; por eso se tratan ambos como límite de consulta. Los números exactos de límite
  no se pudieron confirmar en la documentación accesible durante la verificación.
- Catálogo KEV de CISA: https://www.cisa.gov/known-exploited-vulnerabilities-catalog
  JSON: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json. Campos según el
  esquema oficial: catalogVersion, dateReleased, count, vulnerabilities[] con cveID, vendorProject, product,
  vulnerabilityName, dateAdded, shortDescription, requiredAction, dueDate, knownRansomwareCampaignUse, notes, cwes.

El catálogo KEV se puede pasar ya descargado (KevCatalog) para no repetir la descarga de un archivo grande.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import httpx
from pydantic import BaseModel, Field

from aleph.core.schemas import EntityRecord, RelationRecord

from ._http import get_json, open_client
from .errors import InvalidInputError, OsintError, UpstreamError

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
_CVE_RE = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}", re.IGNORECASE)
_NVD_LIMIT_STATUSES = (403, 429)


class CvssInfo(BaseModel):
    version: str  # 3.1, 3.0 o 2.0
    score: float
    severity: str = ""
    vector: str = ""


class KevEntry(BaseModel):
    cve_id: str
    vendor: str = ""
    product: str = ""
    name: str = ""
    short_description: str = ""
    date_added: date | None = None  # desde cuándo figura en KEV
    due_date: date | None = None
    ransomware_use: str = ""  # knownRansomwareCampaignUse: "Known" o "Unknown"
    required_action: str = ""


class KevCatalog:
    """Catálogo KEV ya parseado, indexado por CVE."""

    def __init__(self, entries: dict[str, KevEntry], *, catalog_version: str = "",
                 date_released: str = "", skipped: int = 0):
        self.entries = entries
        self.catalog_version = catalog_version
        self.date_released = date_released
        self.skipped = skipped


class CveReport(BaseModel):
    cve_id: str
    found_in_nvd: bool
    description: str = ""
    description_lang: str = ""
    published: str = ""
    last_modified: str = ""
    nvd_status: str = ""
    cvss: CvssInfo | None = None
    cwes: list[str] = Field(default_factory=list)
    kev_checked: bool = False
    in_kev: bool = False
    kev: KevEntry | None = None
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


def normalize_cve(value: str) -> str:
    text = (value or "").strip()
    if not _CVE_RE.fullmatch(text):
        raise InvalidInputError("identificador CVE inválido: se espera CVE-AAAA-NNNN")
    return text.upper()


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_kev(data: Any) -> KevCatalog:
    """Parsea el JSON del catálogo KEV. Los elementos mal formados se cuentan y se omiten."""
    if not isinstance(data, dict) or not isinstance(data.get("vulnerabilities"), list):
        raise UpstreamError("el catálogo KEV no tiene la lista «vulnerabilities»", url=KEV_URL)
    entries: dict[str, KevEntry] = {}
    skipped = 0
    for item in data["vulnerabilities"]:
        if not isinstance(item, dict) or not isinstance(item.get("cveID"), str):
            skipped += 1
            continue
        cve = item["cveID"].strip().upper()
        entries[cve] = KevEntry(
            cve_id=cve,
            vendor=str(item.get("vendorProject") or ""),
            product=str(item.get("product") or ""),
            name=str(item.get("vulnerabilityName") or ""),
            short_description=str(item.get("shortDescription") or ""),
            date_added=_parse_date(item.get("dateAdded")),
            due_date=_parse_date(item.get("dueDate")),
            ransomware_use=str(item.get("knownRansomwareCampaignUse") or ""),
            required_action=str(item.get("requiredAction") or ""),
        )
    return KevCatalog(
        entries, catalog_version=str(data.get("catalogVersion") or ""),
        date_released=str(data.get("dateReleased") or ""), skipped=skipped,
    )


async def fetch_kev(*, client: httpx.AsyncClient | None = None) -> KevCatalog:
    async with open_client(client) as http:
        data = await get_json(http, KEV_URL)
    return parse_kev(data)


def _description(cve: dict[str, Any]) -> tuple[str, str]:
    items = [d for d in cve.get("descriptions") or [] if isinstance(d, dict)]
    for wanted in ("es", "en"):
        for item in items:
            if item.get("lang") == wanted and item.get("value"):
                return str(item["value"]), wanted
    for item in items:
        if item.get("value"):
            return str(item["value"]), str(item.get("lang") or "")
    return "", ""


def _cvss(metrics: Any) -> CvssInfo | None:
    if not isinstance(metrics, dict):
        return None
    for key, version in (("cvssMetricV31", "3.1"), ("cvssMetricV30", "3.0"), ("cvssMetricV2", "2.0")):
        items = [m for m in metrics.get(key) or [] if isinstance(m, dict)]
        if not items:
            continue
        chosen = next((m for m in items if m.get("type") == "Primary"), items[0])
        data = chosen.get("cvssData") if isinstance(chosen.get("cvssData"), dict) else {}
        score = data.get("baseScore")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            severity = data.get("baseSeverity") or chosen.get("baseSeverity") or ""
            return CvssInfo(version=version, score=float(score), severity=str(severity),
                            vector=str(data.get("vectorString") or ""))
    return None


def _cwes(cve: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for weakness in cve.get("weaknesses") or []:
        if not isinstance(weakness, dict):
            continue
        for desc in weakness.get("description") or []:
            if isinstance(desc, dict) and isinstance(desc.get("value"), str):
                found.append(desc["value"])
    return list(dict.fromkeys(found))


async def lookup_cve(
    cve_id: str,
    *,
    client: httpx.AsyncClient | None = None,
    kev: KevCatalog | None = None,
    api_key: str = "",
) -> CveReport:
    """Descripción, CVSS y estado KEV de un CVE. Si no se pasa `kev`, se descarga el catálogo."""
    cve = normalize_cve(cve_id)
    warnings: list[str] = []
    raw: dict[str, Any] = {}
    headers = {"apiKey": api_key} if api_key else None
    async with open_client(client) as http:
        payload = await get_json(http, NVD_URL, params={"cveId": cve}, headers=headers,
                                 rate_limit_statuses=_NVD_LIMIT_STATUSES)
        raw["nvd"] = payload
        catalog = kev
        kev_checked = catalog is not None
        if catalog is None:
            try:
                catalog = await fetch_kev(client=http)
                kev_checked = True
            except OsintError as exc:
                warnings.append(f"catálogo KEV no disponible: {exc}")

    entries = payload.get("vulnerabilities") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise UpstreamError("respuesta de NVD sin la lista «vulnerabilities»", url=NVD_URL)
    cve_data: dict[str, Any] | None = None
    for entry in entries:
        candidate = entry.get("cve") if isinstance(entry, dict) else None
        if isinstance(candidate, dict) and str(candidate.get("id", "")).upper() == cve:
            cve_data = candidate
            break

    kev_entry = catalog.entries.get(cve) if catalog is not None else None
    if kev_entry is not None:
        raw["kev"] = kev_entry.model_dump(mode="json")
    if cve_data is None:
        warnings.append(f"{cve} no figura en NVD")
        cve_data = {}
    description, lang = _description(cve_data)
    cvss = _cvss(cve_data.get("metrics"))
    cwes = _cwes(cve_data)

    entities = [EntityRecord(
        type="vulnerability", label=cve, ref="cve", confidence=1.0,
        props={
            "description": description, "cvss_score": cvss.score if cvss else None,
            "cvss_severity": cvss.severity if cvss else "", "in_kev": kev_entry is not None,
            "kev_date_added": kev_entry.date_added.isoformat() if kev_entry and kev_entry.date_added else "",
        },
    )]
    relations: list[RelationRecord] = []
    if kev_entry is not None and kev_entry.vendor:
        entities.append(EntityRecord(type="organization", label=kev_entry.vendor, ref="vendor",
                                     confidence=1.0, props={"fuente": "CISA KEV"}))
        relations.append(RelationRecord(src_ref="cve", dst_ref="vendor", type="affects", confidence=1.0,
                                        props={"product": kev_entry.product}))
    return CveReport(
        cve_id=cve, found_in_nvd=bool(cve_data), description=description, description_lang=lang,
        published=str(cve_data.get("published") or ""), last_modified=str(cve_data.get("lastModified") or ""),
        nvd_status=str(cve_data.get("vulnStatus") or ""), cvss=cvss, cwes=cwes,
        kev_checked=kev_checked, in_kev=kev_entry is not None, kev=kev_entry,
        entities=entities, relations=relations, warnings=warnings, raw=raw,
    )
