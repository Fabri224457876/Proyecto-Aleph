"""Fusión calibrada de señales: regresión logística con indicadores de disponibilidad.

logit = sesgo + contexto + Σ_j disponible_j · (w_j · score_j + c_j) + Σ_j cohorte_j · wz_j · z_j

- Cada señal aporta solo si está disponible, así que el puntaje se calcula con cualquier subconjunto.
- z_j es el puntaje normalizado contra la cohorte (cuánto se destaca este par respecto de los demás
  pares de cada cuenta); solo existe cuando se analiza un conjunto grande.
- Las pendientes w_j y wz_j se restringen a ≥ 0: parecerse nunca resta.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

WEIGHTS_PATH = Path(__file__).with_name("weights.json")
CONTEXT = ("same_platform", "log_min_posts")
_FAMILY_DEFAULT = {"stylometry": 1.2, "temporal": 0.8, "behavior": 0.8, "network": 1.5,
                   "profile": 1.2, "neural": 2.0}
Z_TRIM = 3
Z_MIN_COHORT = 12


@dataclass
class FusionModel:
    bias: float = -4.0
    context: dict[str, float] = field(default_factory=dict)
    signals: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # w, c, wz
    trained: bool = False

    def coef(self, name: str, family: str) -> tuple[float, float, float]:
        if name in self.signals:
            return self.signals[name]
        w = _FAMILY_DEFAULT.get(family, 1.0)
        # Reserva para señales sin peso entrenado: centrada en 0.5, pendiente moderada.
        return (w, -0.5 * w, 0.0) if (not self.trained or family == "neural") else (0.0, 0.0, 0.0)

    def to_json(self) -> dict[str, Any]:
        return {"bias": self.bias, "context": self.context,
                "signals": {k: {"w": v[0], "c": v[1], "wz": v[2]} for k, v in self.signals.items()}}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "FusionModel":
        return cls(bias=float(d["bias"]), context={k: float(v) for k, v in d["context"].items()},
                   signals={k: (float(v["w"]), float(v["c"]), float(v.get("wz", 0.0)))
                            for k, v in d["signals"].items()}, trained=True)


@dataclass
class Weights:
    cohort: FusionModel = field(default_factory=FusionModel)
    pairwise: FusionModel = field(default_factory=FusionModel)
    ref: dict[str, Any] = field(default_factory=dict)  # estadísticas de referencia de hábitos
    train_prior: float = 0.01
    source: str = "fallback"


_CACHE: dict[str, Weights] = {}


def load_weights(path: Path | str | None = None) -> Weights:
    """Carga los pesos del paquete; si faltan o están corruptos, usa pesos de reserva razonables."""
    p = Path(path) if path else WEIGHTS_PATH
    key = str(p)
    if key in _CACHE:
        return _CACHE[key]
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        w = Weights(cohort=FusionModel.from_json(d["models"]["cohort"]),
                    pairwise=FusionModel.from_json(d["models"]["pairwise"]),
                    ref={"habits": d.get("habits", {})},
                    train_prior=float(d.get("train_prior", 0.01)), source=p.name)
    except (OSError, ValueError, KeyError, TypeError):
        w = Weights()
    _CACHE[key] = w
    return w


def clear_cache() -> None:
    _CACHE.clear()


def cohort_z(score: np.ndarray, avail: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Normaliza cada puntaje contra los demás pares de ambas cuentas (media recortada)."""
    n = score.shape[0]
    cnt = avail.sum(1)
    ok = cnt >= Z_MIN_COHORT + Z_TRIM
    if n <= Z_TRIM + 1 or not ok.any():
        return np.zeros_like(score, dtype=float), np.zeros_like(avail)
    y = np.where(avail, score, -np.inf)
    thr = np.partition(y, n - Z_TRIM, axis=1)[:, n - Z_TRIM]
    keep = avail & (y < thr[:, None])
    kc = np.maximum(keep.sum(1), 1)
    mu = (score * keep).sum(1) / kc
    sd = np.sqrt((((score - mu[:, None]) ** 2) * keep).sum(1) / kc)
    sd = np.maximum(sd, 0.02)
    za = (score - mu[:, None]) / sd[:, None]
    z = 0.5 * (za + za.T)
    ok &= keep.sum(1) >= Z_MIN_COHORT
    return np.clip(z, -3.0, 8.0) / 8.0, avail & np.outer(ok, ok)


def context_features(same_platform: np.ndarray, n_posts: np.ndarray) -> dict[str, np.ndarray]:
    mp = np.minimum(n_posts[:, None], n_posts[None, :])
    return {"same_platform": same_platform.astype(float), "log_min_posts": np.log1p(mp) / 5.0}


def logit(model: FusionModel, sigs: list[tuple[str, str, np.ndarray, np.ndarray]],
          zfeat: dict[str, tuple[np.ndarray, np.ndarray]], ctx: dict[str, np.ndarray]) -> np.ndarray:
    """sigs: (nombre, familia, score, disponible). Funciona con matrices N×N o con escalares."""
    out = model.bias + sum(model.context.get(k, 0.0) * v for k, v in ctx.items())
    for name, family, s, a in sigs:
        w, c, wz = model.coef(name, family)
        out = out + a * (w * s + c)
        if wz and name in zfeat:
            z, za = zfeat[name]
            out = out + za * (wz * z)
    return out


# ------------------------------------------------------------------------------------------------
# entrenamiento
# ------------------------------------------------------------------------------------------------
def fit(x_sig: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
        ctx: dict[str, np.ndarray], y: np.ndarray, use_z: bool, l2: float = 1e-5,
        sample_weight: np.ndarray | None = None) -> FusionModel:
    """x_sig[nombre] = (score, disponible, z, z_disponible), vectores por par. Devuelve el modelo.

    `sample_weight` permite submuestrear negativos sin alterar el prior (peso = 1 / fracción)."""
    names = sorted(x_sig)
    cols = [np.ones(len(y))] + [ctx[k] for k in CONTEXT]
    lower = [-np.inf] * len(cols)
    for nme in names:
        s, a, z, za = x_sig[nme]
        cols += [a * s, a.astype(float)]
        lower += [0.0, -np.inf]
        if use_z:
            cols.append(za * z)
            lower.append(0.0)
    x = np.column_stack(cols).astype(np.float64)
    yv = y.astype(np.float64)
    sw = np.ones(len(yv)) if sample_weight is None else sample_weight.astype(np.float64)
    n = sw.sum()
    reg = np.full(x.shape[1], l2)
    reg[0] = 0.0

    def loss(beta: np.ndarray) -> tuple[float, np.ndarray]:
        zz = x @ beta
        ll = np.logaddexp(0.0, zz) - yv * zz
        p = 1.0 / (1.0 + np.exp(-zz))
        return (sw * ll).sum() / n + 0.5 * (reg * beta * beta).sum(), x.T @ (sw * (p - yv)) / n + reg * beta

    b0 = np.zeros(x.shape[1])
    base = float((sw * yv).sum() / n)
    b0[0] = np.log(max(base, 1e-6) / max(1 - base, 1e-6))
    res = minimize(loss, b0, jac=True, method="L-BFGS-B",
                   bounds=[(lo, None) for lo in lower], options={"maxiter": 3000, "maxfun": 6000})
    beta = res.x
    model = FusionModel(bias=float(beta[0]), trained=True,
                        context={k: float(beta[1 + i]) for i, k in enumerate(CONTEXT)})
    pos = 1 + len(CONTEXT)
    for nme in names:
        w, c = float(beta[pos]), float(beta[pos + 1])
        pos += 2
        wz = 0.0
        if use_z:
            wz = float(beta[pos])
            pos += 1
        model.signals[nme] = (w, c, wz)
    return model


def save_weights(cohort: FusionModel, pairwise: FusionModel, habits: dict[str, Any],
                 train_prior: float, meta: dict[str, Any], path: Path | str | None = None) -> Path:
    p = Path(path) if path else WEIGHTS_PATH
    payload = {"version": 1, "meta": meta, "train_prior": train_prior,
               "models": {"cohort": cohort.to_json(), "pairwise": pairwise.to_json()},
               "habits": habits}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    clear_cache()
    return p
