"""Regenera el índice embebido de MITRE ATT&CK (Enterprise) del paquete CTI.

Lee el bundle STIX oficial `enterprise-attack.json` del repositorio
`mitre-attack/attack-stix-data`, ya sea desde una ruta local (`--input`) o descargándolo
de un tag (`--tag`, solo lectura, sin guardar el archivo en disco) y escribe
`data/attack_enterprise.json`, que es lo que carga `aleph.cti.attack`.

Nunca se ejecuta al importar el paquete. Uso desde la raíz del repo:

    .venv/Scripts/python -m aleph.cti.build_attack_index --input RUTA/enterprise-attack.json
    .venv/Scripts/python -m aleph.cti.build_attack_index --tag v19.2

Se excluyen las técnicas revocadas (`revoked`). Las deprecadas se conservan con
`deprecated: true` porque siguen apareciendo en informes viejos.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

OFFICIAL_RAW_URL = (
    "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/{ref}/"
    "enterprise-attack/enterprise-attack.json"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "attack_enterprise.json"
DOMAIN = "enterprise-attack"
_TECHNIQUE_ID = re.compile(r"^T(\d{4})(?:\.(\d{3}))?$")


def _mitre_ref(obj: dict[str, Any]) -> dict[str, Any] | None:
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack" and ref.get("external_id"):
            return ref
    return None


def _sort_key(technique_id: str) -> tuple[int, int]:
    match = _TECHNIQUE_ID.match(technique_id)
    if not match:
        raise ValueError(f"ID ATT&CK con formato inesperado: {technique_id!r}")
    return int(match.group(1)), int(match.group(2) or 0)


def build_index(bundle: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Convierte un bundle STIX de ATT&CK Enterprise en el índice compacto del paquete."""
    objects = bundle.get("objects")
    if not isinstance(objects, list):
        raise ValueError("El archivo no es un bundle STIX (falta 'objects').")

    collections = [o for o in objects if o.get("type") == "x-mitre-collection"]
    enterprise = [c for c in collections if DOMAIN in c.get("x_mitre_domains", [DOMAIN])]
    if len(enterprise) != 1:
        raise ValueError(f"Se esperaba una colección Enterprise, hay {len(enterprise)}.")
    version = str(enterprise[0]["x_mitre_version"])

    tactics: dict[str, dict[str, str]] = {}
    for obj in objects:
        if obj.get("type") != "x-mitre-tactic" or obj.get("revoked"):
            continue
        ref = _mitre_ref(obj)
        shortname = obj.get("x_mitre_shortname")
        if not ref or not shortname:
            continue
        tactics[shortname] = {
            "shortname": shortname,
            "id": ref["external_id"],
            "name": obj["name"],
            "url": ref.get("url", ""),
        }

    stix_by_id: dict[str, str] = {}
    techniques: list[dict[str, Any]] = []
    for obj in objects:
        if obj.get("type") != "attack-pattern" or obj.get("revoked"):
            continue
        if DOMAIN not in obj.get("x_mitre_domains", [DOMAIN]):
            continue
        ref = _mitre_ref(obj)
        if ref is None:
            continue
        technique_id = ref["external_id"]
        if not _TECHNIQUE_ID.match(technique_id):
            raise ValueError(f"ID de técnica inesperado en el bundle: {technique_id!r}")
        if technique_id in stix_by_id:
            raise ValueError(f"ID de técnica duplicado en el bundle: {technique_id}")
        stix_by_id[technique_id] = obj["id"]
        phases = [
            k["phase_name"]
            for k in obj.get("kill_chain_phases", [])
            if k.get("kill_chain_name") == "mitre-attack"
        ]
        unknown = [p for p in phases if p not in tactics]
        if unknown:
            raise ValueError(f"{technique_id} usa tácticas desconocidas: {unknown}")
        is_sub = bool(obj.get("x_mitre_is_subtechnique"))
        techniques.append({
            "id": technique_id,
            "name": obj["name"],
            "stix_id": obj["id"],
            "tactics": phases,
            "parent": technique_id.split(".")[0] if is_sub else None,
            "url": ref.get("url", ""),
            "deprecated": bool(obj.get("x_mitre_deprecated")),
        })

    techniques.sort(key=lambda t: _sort_key(t["id"]))
    subs = sum(1 for t in techniques if t["parent"])
    return {
        "attack_version": version,
        "domain": DOMAIN,
        "source": source,
        "license_note": "Datos de MITRE ATT&CK, sujetos a sus términos de uso.",
        "counts": {
            "tactics": len(tactics),
            "techniques": len(techniques) - subs,
            "subtechniques": subs,
            "deprecated": sum(1 for t in techniques if t["deprecated"]),
        },
        "tactics": [tactics[k] for k in sorted(tactics, key=lambda k: tactics[k]["id"])],
        "techniques": techniques,
    }


def _load_bundle(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if args.input:
        path = Path(args.input)
        with path.open(encoding="utf-8") as fh:
            return json.load(fh), f"archivo local: {path.name}"
    url = OFFICIAL_RAW_URL.format(ref=args.tag)
    resp = httpx.get(url, timeout=180, follow_redirects=True)
    resp.raise_for_status()
    return resp.json(), f"{url} (solo lectura)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Ruta a enterprise-attack.json ya descargado.")
    source.add_argument("--tag", help="Tag de attack-stix-data, p. ej. v19.2.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    bundle, source_label = _load_bundle(args)
    index = build_index(bundle, source=source_label)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(
        f"ATT&CK {index['attack_version']}: {index['counts']} -> {out}",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
