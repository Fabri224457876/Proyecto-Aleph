"""Cargador de datasets públicos de verificación de autoría en formato PAN.

No descarga nada: recibe rutas a archivos que aporta el operador. Formatos soportados:

1. PAN 2020–2023 (JSON Lines):
   - pares:   {"id": "...", "fandoms": [...], "pair": ["texto 1", "texto 2"]}
   - verdad:  {"id": "...", "same": true, "authors": ["a", "b"]}
2. PAN 2013–2015 (carpetas): <raíz>/<PROBLEMA>/known*.txt + unknown.txt y <raíz>/truth.txt con
   líneas "<PROBLEMA> Y|N".

Cada texto se convierte en una pseudo-cuenta (sin fechas ni perfil), así que solo aplican las
señales de estilometría. Uso previsto:

    from aleph.menard.pan import evaluate_pan
    print(evaluate_pan("pairs.jsonl", "truth.jsonl", limit=2000))
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from aleph.core.schemas import AccountProfile, AccountRecord, PostRecord

_SENT = re.compile(r"(?<=[.!?…])\s+|\n+")


@dataclass
class PanPair:
    id: str
    texts: tuple[str, str]
    same: bool | None = None  # None si no hay archivo de verdad
    meta: dict[str, Any] = field(default_factory=dict)


def iter_pan_jsonl(pairs_path: str | Path, truth_path: str | Path | None = None) -> Iterator[PanPair]:
    truth: dict[str, dict[str, Any]] = {}
    if truth_path is not None:
        with open(truth_path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    truth[str(row["id"])] = row
    with open(pairs_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            pid = str(row["id"])
            t = truth.get(pid, {})
            a, b = row["pair"]
            yield PanPair(id=pid, texts=(a, b), same=bool(t["same"]) if "same" in t else None,
                          meta={k: v for k, v in {**row, **t}.items() if k not in ("pair", "id", "same")})


def iter_pan_folders(root: str | Path) -> Iterator[PanPair]:
    root = Path(root)
    truth: dict[str, bool] = {}
    tfile = root / "truth.txt"
    if tfile.exists():
        for line in tfile.read_text(encoding="utf-8-sig").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                truth[parts[0]] = parts[1].upper().startswith("Y")
    for prob in sorted(p for p in root.iterdir() if p.is_dir()):
        unknown = prob / "unknown.txt"
        known = sorted(prob.glob("known*.txt"))
        if not unknown.exists() or not known:
            continue
        yield PanPair(id=prob.name,
                      texts=("\n".join(k.read_text(encoding="utf-8-sig", errors="replace") for k in known),
                             unknown.read_text(encoding="utf-8-sig", errors="replace")),
                      same=truth.get(prob.name), meta={"known_docs": len(known)})


def iter_pan(path: str | Path, truth_path: str | Path | None = None) -> Iterator[PanPair]:
    """Detecta el formato por el tipo de ruta (archivo .jsonl o carpeta)."""
    return iter_pan_folders(path) if Path(path).is_dir() else iter_pan_jsonl(path, truth_path)


def text_to_profile(text: str, handle: str, max_chars: int = 280) -> AccountProfile:
    """Parte un documento en "publicaciones" de hasta `max_chars` respetando oraciones."""
    chunks: list[str] = []
    cur = ""
    for sent in (s.strip() for s in _SENT.split(text)):
        if not sent:
            continue
        while len(sent) > max_chars:
            chunks.append(sent[:max_chars])
            sent = sent[max_chars:]
        if cur and len(cur) + 1 + len(sent) > max_chars:
            chunks.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        chunks.append(cur)
    posts = [PostRecord(platform_post_id=f"{handle}-{i}", text=c) for i, c in enumerate(chunks)]
    return AccountProfile(account=AccountRecord(platform="pan", handle=handle), posts=posts)


def pair_profiles(pair: PanPair, max_chars: int = 280) -> tuple[AccountProfile, AccountProfile]:
    safe = re.sub(r"[^A-Za-z0-9]", "", pair.id) or "x"
    return (text_to_profile(pair.texts[0], f"p{safe}a", max_chars),
            text_to_profile(pair.texts[1], f"p{safe}b", max_chars))


def evaluate_pan(path: str | Path, truth_path: str | Path | None = None, limit: int | None = None,
                 max_chars: int = 280) -> dict[str, Any]:
    """Puntúa los pares con las señales de estilometría y devuelve AUC, EER, precisión y recall.

    Todos los textos se analizan juntos para que sirvan de cohorte (rareza de rasgos). Atención: los
    pesos del paquete se ajustaron con datos sintéticos de redes sociales; en otro género (fanfiction,
    ensayos) el umbral 0.5 no está calibrado y conviene mirar AUC/EER o reentrenar.
    """
    from .engine import analyze_matrix
    from .eval import summarize

    pairs = []
    for k, p in enumerate(iter_pan(path, truth_path)):
        if limit is not None and k >= limit:
            break
        pairs.append(p)
    profiles: list[AccountProfile] = []
    for p in pairs:
        profiles.extend(pair_profiles(p, max_chars))
    an = analyze_matrix(profiles, min_posts=1, families=["stylometry"])
    idx = {k: i for i, k in enumerate(an.keys)}
    scores, labels, ids = [], [], []
    for p, (a, b) in zip(pairs, zip(profiles[0::2], profiles[1::2])):
        if a.key in idx and b.key in idx:
            scores.append(float(an.scores[idx[a.key], idx[b.key]]))
            labels.append(p.same)
            ids.append(p.id)
    out: dict[str, Any] = {"pairs": len(ids), "scores": dict(zip(ids, scores)), "mode": an.mode}
    if ids and all(v is not None for v in labels):
        out["metrics"] = summarize(np.array(labels, bool), np.array(scores))
    return out
