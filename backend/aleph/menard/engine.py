"""Orquestación de MENARD: rasgos → señales → fusión → pares y clusters.

Las salidas son hipótesis con evidencia para revisión humana, nunca afirmaciones de identidad.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import networkx as nx
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from aleph.core.schemas import (
    AccountProfile,
    ClusterResult,
    Evidence,
    MenardReport,
    PairResult,
    SignalResult,
)

from . import fusion
from .features import AccountFeatures, extract
from .neural import registered_neural_signals
from .signals import Corpus, SignalMatrix, compute_all

DEFAULT_MIN_POSTS = 5
DEFAULT_THRESHOLD = 0.5
DEFAULT_REPORT_MIN_SCORE = 0.2
FAMILIES = ("stylometry", "temporal", "behavior", "network", "profile", "neural")

SIGNAL_LABELS = {
    "stylometry.char_ngrams": "n-gramas de caracteres",
    "stylometry.function_words": "palabras función",
    "stylometry.punctuation": "puntuación",
    "stylometry.capitalization": "mayúsculas",
    "stylometry.elongation": "alargamientos y risas",
    "stylometry.emoji": "emojis",
    "stylometry.orthography": "ortografía y abreviaturas",
    "stylometry.rioplatense": "marcas dialectales",
    "stylometry.length": "longitud de mensaje",
    "temporal.hourly": "horario de actividad",
    "temporal.weekday": "días de actividad",
    "temporal.sleep_window": "ventana de sueño / huso",
    "temporal.sync_bursts": "ráfagas sincronizadas",
    "temporal.alternation": "alternancia sin solaparse",
    "behavior.client": "cliente/dispositivo",
    "behavior.hashtags": "hashtags",
    "behavior.domains": "dominios enlazados",
    "behavior.targets": "destinatarios",
    "behavior.post_mix": "tipo de publicaciones",
    "network.following": "seguidos en común",
    "network.followers": "seguidores en común",
    "network.mutual_mentions": "menciones mutuas",
    "profile.handle": "handle",
    "profile.display_name": "nombre visible",
    "profile.bio": "biografía",
    "profile.creation_date": "fecha de creación",
    "profile.avatar": "avatar",
}


def _label(name: str) -> str:
    return SIGNAL_LABELS.get(name, name)


def _sigmoid(x: Any) -> Any:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


MAX_REPORTED_SCORE = 0.99


@dataclass
class Analysis:
    """Resultado matricial completo; `analyze` lo resume en un MenardReport."""

    profiles: list[AccountProfile]
    keys: list[str]
    corpus: Corpus
    signals: list[SignalMatrix]
    zfeat: dict[str, tuple[np.ndarray, np.ndarray]]
    ctx: dict[str, np.ndarray]
    model: fusion.FusionModel
    mode: str
    scores: np.ndarray
    skipped: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    prior_shift: float = 0.0
    _baseline: dict[str, float] = field(default_factory=dict)

    def fused(self, families: Iterable[str] | None = None) -> np.ndarray:
        """Puntaje fusionado usando solo las familias indicadas (para ablaciones)."""
        fams = set(families) if families is not None else None
        sigs = [(s.name, s.family, s.score, s.avail) for s in self.signals
                if fams is None or s.family in fams]
        out = _sigmoid(fusion.logit(self.model, sigs, self.zfeat, self.ctx) + self.prior_shift)
        out = np.array(out, dtype=float)
        np.fill_diagonal(out, 0.0)
        return out

    def baseline(self, s: SignalMatrix) -> float:
        """Valor típico de la señal en el conjunto: un par "a favor" es el que lo supera."""
        if s.name not in self._baseline:
            ok = s.avail
            self._baseline[s.name] = (float(s.score[ok].mean())
                                      if self.mode == "cohort" and ok.any() else 0.5)
        return self._baseline[s.name]

    def confidence_limits(self, i: int, j: int) -> list[str]:
        """Motivos por los que la confianza no puede ser alta aunque el puntaje lo sea."""
        posts = int(min(self.corpus.n_posts[i], self.corpus.n_posts[j]))
        fams = {s.family for s in self.signals if s.avail[i, j]}
        limits = []
        if posts < 30:
            limits.append(f"la cuenta con menos actividad tiene {posts} publicaciones (se piden 30)")
        if len(fams) < 3:
            limits.append(f"solo hubo datos para {len(fams)} familia(s) de señales (se piden 3)")
        if self.mode != "cohort":
            limits.append("la comparación se hizo sin conjunto de referencia y no se pudo medir la "
                          "rareza de los rasgos")
        return limits

    def confidence(self, i: int, j: int) -> str:
        score = float(self.scores[i, j])
        posts = min(self.corpus.n_posts[i], self.corpus.n_posts[j])
        fams = {s.family for s in self.signals if s.avail[i, j]}
        if score >= 0.85 and not self.confidence_limits(i, j):
            return "alta"
        if score >= 0.6 and posts >= 10 and len(fams) >= 2:
            return "media"
        return "baja"

    def pair(self, i: int, j: int, evidence: bool = True) -> PairResult:
        results: list[SignalResult] = []
        contrib: list[tuple[float, SignalMatrix]] = []
        for s in self.signals:
            w, c, wz = self.model.coef(s.name, s.family)
            avail = bool(s.avail[i, j])
            expl, ev = "", []
            if avail:
                if evidence:
                    try:
                        expl, ev = s.explain(i, j)
                    except Exception as exc:  # la evidencia nunca debe tirar abajo el análisis
                        expl, ev = f"No se pudo armar la evidencia ({type(exc).__name__}).", []
                k = w * (float(s.score[i, j]) - self.baseline(s))
                if wz and s.name in self.zfeat and self.zfeat[s.name][1][i, j]:
                    k += wz * float(self.zfeat[s.name][0][i, j])
                contrib.append((k, s))
            else:
                expl = "Sin datos suficientes en al menos una de las cuentas."
            results.append(SignalResult(
                name=s.name, family=s.family, score=round(float(s.score[i, j]), 4) if avail else 0.0,
                weight=round(w + wz, 4), available=avail, explanation=expl, evidence=ev))
        # El puntaje informado nunca es certeza: la calibración satura con pares casi idénticos.
        score = min(float(self.scores[i, j]), MAX_REPORTED_SCORE)
        conf = self.confidence(i, j)
        return PairResult(a=self.keys[i], b=self.keys[j], score=round(score, 4), confidence=conf,
                          signals=results, summary=self._summary(i, j, score, conf, contrib))

    def _summary(self, i: int, j: int, score: float, conf: str,
                 contrib: list[tuple[float, SignalMatrix]]) -> str:
        contrib.sort(key=lambda t: -t[0])
        pro = [f"{_label(s.name)} ({s.score[i, j]:.2f})" for k, s in contrib[:4] if k > 0.05]
        con = [f"{_label(s.name)} ({s.score[i, j]:.2f})" for k, s in contrib[::-1][:3] if k < -0.05]
        a, b = self.keys[i], self.keys[j]
        posts = int(min(self.corpus.n_posts[i], self.corpus.n_posts[j]))
        if score >= 0.5:
            head = f"Hipótesis de mismo operador entre {a} y {b}: puntaje {score:.2f}, confianza {conf}."
        elif score >= 0.2:
            head = (f"Hipótesis débil de mismo operador entre {a} y {b}: puntaje {score:.2f}, "
                    f"confianza {conf}.")
        else:
            head = (f"Sin indicios suficientes para sostener la hipótesis de mismo operador entre "
                    f"{a} y {b}: puntaje {score:.2f}.")
        parts = [head]
        if pro:
            parts.append("Señales a favor: " + ", ".join(pro) + ".")
        if con:
            parts.append("Señales en contra: " + ", ".join(con) + ".")
        n_av = len(contrib)
        parts.append(f"Se evaluaron {n_av} de {len(self.signals)} señales.")
        limits = self.confidence_limits(i, j)
        if limits and score >= 0.5:
            parts.append("La confianza no llega a alta porque " + "; ".join(limits) + ".")
        else:
            if posts < 30:
                parts.append(f"Evidencia limitada: la cuenta con menos actividad tiene {posts} "
                             "publicaciones.")
            if self.mode == "pairwise":
                parts.append("Comparación sin conjunto de referencia: no se pudo estimar la rareza "
                             "de los rasgos.")
        if not self.model.trained:
            parts.append("Pesos de reserva (sin calibrar).")
        parts.append("Es una hipótesis para revisión de un analista, no una identificación.")
        return " ".join(parts)


def _neural_signals(profiles: list[AccountProfile], warnings: list[str]) -> list[SignalMatrix]:
    out = []
    n = len(profiles)
    for name, fn in registered_neural_signals().items():
        try:
            res = fn(profiles)
            score = np.asarray(res.score, dtype=float)
            if score.shape != (n, n):
                raise ValueError(f"forma {score.shape}, se esperaba {(n, n)}")
            avail = np.ones((n, n), bool) if res.available is None else np.asarray(res.available, bool)
            avail = avail & ~np.eye(n, dtype=bool) & ~np.isnan(score)
            score = np.clip(np.nan_to_num(score), 0.0, 1.0)

            def explain(i: int, j: int, _r=res) -> tuple[str, list[Evidence]]:
                return _r.explanation, (_r.evidence(i, j) if _r.evidence else [])

            out.append(SignalMatrix(name, "neural", np.where(avail, score, 0.0), avail, explain))
        except Exception as exc:
            warnings.append(f"Señal {name} omitida: {type(exc).__name__}: {exc}")
    return out


def analyze_matrix(
    profiles: Sequence[AccountProfile],
    *,
    min_posts: int = DEFAULT_MIN_POSTS,
    use_cohort: bool = True,
    families: Iterable[str] | None = None,
    prior: float | None = None,
    weights: fusion.Weights | None = None,
    features: dict[str, AccountFeatures] | None = None,
) -> Analysis:
    """Calcula todas las señales y el puntaje fusionado para todos los pares elegibles."""
    w = weights or fusion.load_weights()
    skipped: dict[str, str] = {}
    eligible: list[AccountProfile] = []
    seen: set[str] = set()
    for p in profiles:
        if p.key in seen:
            skipped[p.key] = "clave duplicada: se analizó solo la primera aparición"
            continue
        seen.add(p.key)
        if len(p.posts) < min_posts:
            skipped[p.key] = f"pocas publicaciones ({len(p.posts)} < {min_posts})"
            continue
        eligible.append(p)
    feats = [features[p.key] if features and p.key in features else extract(p) for p in eligible]
    corpus = Corpus(feats, ref=w.ref, use_cohort=use_cohort)
    warnings: list[str] = []
    sigs = compute_all(corpus) + _neural_signals(eligible, warnings) if corpus.n >= 2 else []
    if families is not None:
        fams = set(families)
        sigs = [s for s in sigs if s.family in fams]
    mode = "cohort" if corpus.cohort else "pairwise"
    model = w.cohort if corpus.cohort else w.pairwise
    zfeat = {s.name: fusion.cohort_z(s.score, s.avail) for s in sigs} if corpus.cohort else {}
    ctx = fusion.context_features(corpus.same_platform, corpus.n_posts)
    shift = 0.0
    if prior is not None and 0 < prior < 1 and 0 < w.train_prior < 1:
        shift = float(np.log(prior / (1 - prior)) - np.log(w.train_prior / (1 - w.train_prior)))
    an = Analysis(profiles=eligible, keys=[p.key for p in eligible], corpus=corpus, signals=sigs,
                  zfeat=zfeat, ctx=ctx, model=model, mode=mode,
                  scores=np.zeros((corpus.n, corpus.n)), skipped=skipped, warnings=warnings,
                  prior_shift=shift)
    if corpus.n >= 2:
        an.scores = an.fused()
    return an


def build_clusters(keys: list[str], scores: np.ndarray, threshold: float,
                   platforms: list[str] | None = None) -> list[ClusterResult]:
    """Componentes sobre pares ≥ umbral, refinados con enlace promedio para no encadenar grupos:
    dos subgrupos solo quedan juntos si el puntaje medio entre ellos alcanza el umbral."""
    g = nx.Graph()
    ii, jj = np.where(np.triu(scores >= threshold, 1))
    g.add_edges_from(zip(ii.tolist(), jj.tolist()))
    groups: list[list[int]] = []
    for comp in nx.connected_components(g):
        idx = sorted(comp)
        if len(idx) <= 2:
            groups.append(idx)
            continue
        sub = scores[np.ix_(idx, idx)]
        dist = 1.0 - sub
        np.fill_diagonal(dist, 0.0)
        labels = fcluster(linkage(squareform(dist, checks=False), "average"),
                          t=1.0 - threshold, criterion="distance")
        for lab in np.unique(labels):
            groups.append([idx[k] for k in np.where(labels == lab)[0]])
    out: list[ClusterResult] = []
    for idx in groups:
        if len(idx) < 2:
            continue
        sub = scores[np.ix_(idx, idx)]
        iu = np.triu_indices(len(idx), 1)
        vals = sub[iu]
        k = int(np.argmin(vals))
        weak = (keys[idx[iu[0][k]]], keys[idx[iu[1][k]]])
        plats = sorted({platforms[i] for i in idx}) if platforms else []
        summary = (f"Hipótesis de mismo operador para {len(idx)} cuentas"
                   + (f" ({', '.join(plats)})" if plats else "")
                   + f"; cohesión media {vals.mean():.2f}; vínculo más débil {weak[0]} – {weak[1]} "
                     f"({vals.min():.2f}). Requiere revisión de un analista.")
        out.append(ClusterResult(members=[keys[i] for i in idx], cohesion=round(float(vals.mean()), 4),
                                 summary=summary))
    out.sort(key=lambda c: (-len(c.members), -c.cohesion))
    return out


def analyze(
    profiles: Sequence[AccountProfile],
    *,
    min_posts: int = DEFAULT_MIN_POSTS,
    threshold: float = DEFAULT_THRESHOLD,
    report_min_score: float = DEFAULT_REPORT_MIN_SCORE,
    max_pairs: int = 500,
    evidence: bool = True,
    families: Iterable[str] | None = None,
    prior: float | None = None,
    use_cohort: bool = True,
) -> MenardReport:
    """Compara todos los pares elegibles, fusiona señales y arma clusters.

    - `min_posts`: cuentas con menos publicaciones se omiten (quedan en `skipped`).
    - `threshold`: puntaje a partir del cual un par entra a un cluster.
    - `report_min_score` / `max_pairs`: qué pares se devuelven con desglose y evidencia.
    - `families`: restringe las familias de señales usadas.
    - `prior`: proporción esperada de pares del mismo operador, si se conoce; desplaza el puntaje
      respecto del prior de entrenamiento.
    """
    an = analyze_matrix(profiles, min_posts=min_posts, use_cohort=use_cohort, families=families,
                        prior=prior)
    n = an.corpus.n
    pairs: list[PairResult] = []
    clusters: list[ClusterResult] = []
    if n >= 2:
        floor = min(report_min_score, threshold)
        ii, jj = np.where(np.triu(an.scores >= floor, 1))
        order = np.argsort(-an.scores[ii, jj], kind="stable")[:max_pairs]
        pairs = [an.pair(int(ii[k]), int(jj[k]), evidence=evidence) for k in order]
        clusters = build_clusters(an.keys, an.scores, threshold,
                                  [f.platform for f in an.corpus.feats])
    params = {
        "min_posts": min_posts, "threshold": threshold, "report_min_score": report_min_score,
        "max_pairs": max_pairs, "mode": an.mode, "accounts_analyzed": n,
        "pairs_compared": n * (n - 1) // 2, "weights": fusion.load_weights().source,
        "calibrated": an.model.trained, "prior": prior,
        "families": sorted(set(families)) if families is not None else list(FAMILIES),
        "signals": [s.name for s in an.signals], "warnings": an.warnings,
    }
    return MenardReport(pairs=pairs, clusters=clusters, skipped=an.skipped, params=params)


def compare(a: AccountProfile, b: AccountProfile, *,
            background: Sequence[AccountProfile] | None = None) -> PairResult:
    """Compara un par. Con `background` (otras cuentas del mismo contexto) se estima la rareza de los
    rasgos; sin él se usan estadísticas de referencia del paquete y la confianza queda acotada."""
    if a.key == b.key:
        raise ValueError("compare() necesita dos cuentas distintas")
    extra = [p for p in (background or []) if p.key not in (a.key, b.key)]
    an = analyze_matrix([a, b, *extra], min_posts=0)
    return an.pair(0, 1)
