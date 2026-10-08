"""Señales de MENARD. Cada señal produce una matriz N×N de similitud y una máscara de disponibilidad,
calculadas de forma vectorizada sobre todas las cuentas; la evidencia legible se arma solo para los
pares que se informan (función `explain`)."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np
from rapidfuzz import fuzz, process
from scipy import sparse
from scipy.stats import poisson

from aleph.core.schemas import Evidence

from . import lexicon as lx
from .features import HABIT_LABELS, AccountFeatures, ngram_hashes, ngram_strings

MIN_COHORT = 20  # con menos cuentas no se estima rareza a partir del propio conjunto
Explain = Callable[[int, int], tuple[str, list[Evidence]]]


@dataclass
class SignalMatrix:
    name: str
    family: str
    score: np.ndarray
    avail: np.ndarray
    explain: Explain


class Corpus:
    """Conjunto de cuentas con sus rasgos y las estadísticas de referencia."""

    def __init__(self, feats: list[AccountFeatures], ref: dict[str, Any] | None = None,
                 use_cohort: bool = True):
        self.feats = feats
        self.n = len(feats)
        self.cohort = bool(use_cohort and self.n >= MIN_COHORT)
        self.ref = ref or {}
        plats = sorted({f.platform for f in feats})
        self.platform_names = plats
        self.platform = np.array([plats.index(f.platform) for f in feats], dtype=np.int32)
        self.same_platform = self.platform[:, None] == self.platform[None, :]
        self.n_posts = np.array([f.n_posts for f in feats], dtype=float)
        self.n_authored = np.array([f.n_authored for f in feats], dtype=float)
        self.n_tokens = np.array([f.n_tokens for f in feats], dtype=float)
        self._habit_cache: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._word_df: Counter | None = None

    # -- estandarización de hábitos ---------------------------------------------------------------
    def habit_z(self, group: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Devuelve (Z, media, desvío) por cuenta: desvío respecto de lo habitual en su plataforma."""
        if group in self._habit_cache:
            return self._habit_cache[group]
        m = np.vstack([f.habits[group] for f in self.feats]).astype(float)
        k = m.shape[1]
        mean = np.full((self.n, k), np.nan)
        std = np.full((self.n, k), np.nan)
        ref_g = self.ref.get("habits", {}).get(group, {})

        def ref_stats(plat: str) -> tuple[np.ndarray, np.ndarray] | None:
            r = ref_g.get(plat) or ref_g.get("_all")
            if r and len(r["mean"]) == k:
                return np.array(r["mean"], float), np.array(r["std"], float)
            return None

        def emp(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            sub = m[rows]
            cnt = (~np.isnan(sub)).sum(0)
            with np.errstate(all="ignore"):
                mu = np.where(cnt > 0, np.nansum(sub, 0) / np.maximum(cnt, 1), np.nan)
                sd = np.sqrt(np.nansum((sub - mu) ** 2, 0) / np.maximum(cnt, 1))
            return mu, sd, cnt

        g_mu, g_sd, g_cnt = emp(np.arange(self.n)) if self.cohort else (None, None, None)
        for p, pname in enumerate(self.platform_names):
            rows = np.where(self.platform == p)[0]
            mu = np.full(k, np.nan)
            sd = np.full(k, np.nan)
            r = ref_stats(pname)
            if r is not None:
                mu, sd = r[0].copy(), r[1].copy()
            if self.cohort:
                ok = g_cnt >= 8
                mu[ok], sd[ok] = g_mu[ok], g_sd[ok]
                if len(rows) >= 15:
                    p_mu, p_sd, p_cnt = emp(rows)
                    ok = p_cnt >= 8
                    mu[ok], sd[ok] = p_mu[ok], p_sd[ok]
            mean[rows], std[rows] = mu, sd
        std = np.maximum(std, 1e-3 + 0.05 * np.abs(mean))
        z = np.clip((m - mean) / std, -3.0, 3.0)
        self._habit_cache[group] = (z, mean, m)
        return self._habit_cache[group]

    def word_df(self) -> Counter:
        if self._word_df is None:
            df: Counter = Counter()
            for f in self.feats:
                df.update(f.sets["words"].keys())
            self._word_df = df
        return self._word_df


# ------------------------------------------------------------------------------------------------
# utilidades
# ------------------------------------------------------------------------------------------------
def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def _finish(c: Corpus, name: str, family: str, score: np.ndarray, avail: np.ndarray,
            explain: Explain) -> SignalMatrix:
    score = np.clip(np.nan_to_num(score, nan=0.0), 0.0, 1.0).astype(np.float32)
    avail = avail.copy()
    np.fill_diagonal(avail, False)
    return SignalMatrix(name, family, np.where(avail, score, 0.0).astype(np.float32), avail, explain)


def gram(x: sparse.csr_matrix, n: int) -> np.ndarray:
    """X·Xᵀ denso. Las columnas muy frecuentes van por BLAS denso y las raras por producto disperso."""
    x = x.tocsc()
    df = np.diff(x.indptr)
    keep = df >= 2
    common = keep & (df >= max(8, n // 6))
    rare = keep & ~common
    out = np.zeros((n, n), dtype=np.float64)
    if common.any():
        d = x[:, np.where(common)[0]].toarray()
        out += d @ d.T
    if rare.any():
        r = x[:, np.where(rare)[0]].tocsr()
        out += (r @ r.T).toarray()
    return out


def _snippet(texts: list[str], needle: str, width: int = 110, ci: bool = False) -> str:
    for t in texts:
        hay = t.lower() if ci else t
        pos = hay.find(needle)
        if pos >= 0:
            lo = max(0, pos - width // 2)
            s = t[lo : lo + width].replace("\n", " ")
            return ("…" if lo else "") + s + ("…" if lo + width < len(t) else "")
    return ""


def _word_snippet(texts: list[str], word: str) -> str:
    for t in texts:
        if word in {w.lower() for w in lx.WORD_RE.findall(t)}:
            return t[:120].replace("\n", " ")
    return ""


def _fmt(v: float, kind: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "s/d"
    return f"{100 * v:.0f} %" if kind == "pct" else f"{v:.2f}"


# ------------------------------------------------------------------------------------------------
# señal genérica de hábitos (razón de verosimilitud gaussiana por dimensión)
# ------------------------------------------------------------------------------------------------
RHO0 = 0.6  # correlación intra-autor supuesta para un hábito medido sin ruido
LLR_CLIP = 4.0


def _pair_llr(za: np.ndarray, zb: np.ndarray, r: np.ndarray | float) -> np.ndarray:
    s2 = za * za + zb * zb
    return -0.5 * np.log1p(-r * r) - s2 * (r * r) / (2 * (1 - r * r)) + za * zb * r / (1 - r * r)


def habit_signal(c: Corpus, group: str, name: str, family: str, rel: np.ndarray, valid: np.ndarray,
                 title: str, min_dims: int = 2, example: Callable[[int], str | None] | None = None,
                 fast: bool = False) -> SignalMatrix:
    z, mean, raw = c.habit_z(group)
    v = ~np.isnan(z)
    z0 = np.where(v, z, 0.0)
    r = np.clip(RHO0 * np.sqrt(np.outer(rel, rel)), 0.02, 0.95)
    vf = v.astype(float)
    cnt = vf @ vf.T
    if fast:
        sq = z0 * z0
        s2 = sq @ vf.T + vf @ sq.T
        total = -0.5 * np.log1p(-r * r) * cnt - s2 * (r * r) / (2 * (1 - r * r)) \
            + (z0 @ z0.T) * r / (1 - r * r)
    else:
        total = np.zeros((c.n, c.n))
        for k in range(z.shape[1]):
            if not v[:, k].any():
                continue
            zk = z0[:, k]
            llr = np.clip(_pair_llr(zk[:, None], zk[None, :], r), -LLR_CLIP, LLR_CLIP)
            total += llr * np.outer(vf[:, k], vf[:, k])
    score = _sigmoid(total / np.sqrt(np.maximum(cnt, 1.0)))
    avail = (cnt >= min_dims) & np.outer(valid, valid)
    labels = HABIT_LABELS[group]

    def explain(i: int, j: int) -> tuple[str, list[Evidence]]:
        both = v[i] & v[j]
        llr = np.where(both, np.clip(_pair_llr(z0[i], z0[j], r[i, j]), -LLR_CLIP, LLR_CLIP), 0.0)
        order = np.argsort(-llr)
        ev: list[Evidence] = []
        n_pos = int((llr > 0.5).sum())
        n_neg = int((llr < -0.5).sum())
        for k in order[:4]:
            if llr[k] <= 0.4 or abs(z0[i, k]) < 0.7 or abs(z0[j, k]) < 0.7:
                continue
            lab, kind = labels[k]
            side = "por encima" if z0[i, k] > 0 else "por debajo"
            ex = example(int(k)) if example else None
            ev.append(Evidence(
                description=(f"Rasgo compartido, {side} de lo habitual — {lab}: "
                             f"A {_fmt(raw[i, k], kind)}, B {_fmt(raw[j, k], kind)}; "
                             f"referencia del conjunto {_fmt(mean[i, k], kind)}."),
                a=_snippet(c.feats[i].texts, ex, ci=True) if ex else "",
                b=_snippet(c.feats[j].texts, ex, ci=True) if ex else "",
            ))
        for k in order[::-1][:1]:
            if llr[k] < -1.0:
                lab, kind = labels[k]
                ev.append(Evidence(description=(
                    f"En contra, difieren — {lab}: A {_fmt(raw[i, k], kind)}, "
                    f"B {_fmt(raw[j, k], kind)}."
                )))
        return (f"{title}: {n_pos} rasgos coinciden de forma poco común y {n_neg} difieren, "
                f"sobre {int(both.sum())} comparables."), ev

    return _finish(c, name, family, score, avail, explain)


# ------------------------------------------------------------------------------------------------
# señal genérica de conjuntos ponderados por rareza
# ------------------------------------------------------------------------------------------------
def _set_matrix(c: Corpus, key: str) -> tuple[sparse.csr_matrix, list[str]]:
    vocab: dict[str, int] = {}
    rows, cols, vals = [], [], []
    for i, f in enumerate(c.feats):
        for item, cnt in f.sets[key].items():
            rows.append(i)
            cols.append(vocab.setdefault(item, len(vocab)))
            vals.append(cnt)
    x = sparse.csr_matrix((vals, (rows, cols)), shape=(c.n, max(len(vocab), 1)), dtype=float)
    return x, list(vocab)


def set_signal(c: Corpus, key: str, name: str, family: str, what: str, min_total: int = 3,
               jaccard: bool = False, mask: np.ndarray | None = None) -> SignalMatrix:
    x, items = _set_matrix(c, key)
    df = np.asarray((x > 0).sum(0)).ravel()
    idf = np.log((c.n + 1) / (df + 1)) + 0.1 if c.cohort else np.ones(len(df))
    tot = np.asarray(x.sum(1)).ravel()
    b = (x > 0).astype(float)
    inter = gram(b.multiply(np.sqrt(idf)).tocsr(), c.n)  # masa de rareza de lo compartido
    if jaccard:
        mass = np.asarray(b @ idf).ravel()
        union = mass[:, None] + mass[None, :] - inter
        score = inter / np.maximum(union, 1e-9)
    else:
        xw = x.sqrt().multiply(idf).tocsr()
        norm = np.sqrt(np.asarray(xw.multiply(xw).sum(1)).ravel())
        xw = sparse.diags(1.0 / np.maximum(norm, 1e-9)) @ xw
        score = gram(xw.tocsr(), c.n)
    # Coincidir solo en lo que usa casi todo el conjunto no dice nada: se atenúa según cuánta
    # rareza acumulan los elementos compartidos.
    score = score * np.tanh(inter / 1.5)
    avail = np.outer(tot >= min_total, tot >= min_total)
    if mask is not None:
        avail &= mask

    def explain(i: int, j: int) -> tuple[str, list[Evidence]]:
        a, b = c.feats[i].sets[key], c.feats[j].sets[key]
        idx = {it: n for n, it in enumerate(items)}
        shared = sorted(set(a) & set(b), key=lambda it: -(idf[idx[it]] ** 2) * math.sqrt(a[it] * b[it]))
        ev = [
            Evidence(
                description=(f"Ambas comparten {what} «{it}»"
                             + (f"; aparece en {int(df[idx[it]])} de {c.n} cuentas." if c.cohort else ".")),
                a=f"{a[it]} veces" if not jaccard else "",
                b=f"{b[it]} veces" if not jaccard else "",
            )
            for it in shared[:4]
        ]
        return (f"Comparten {len(shared)} {what}(s) de {len(a)} (A) y {len(b)} (B); "
                "los poco frecuentes en el conjunto pesan más."), ev

    return _finish(c, name, family, score, avail, explain)


def _combine(c: Corpus, name: str, family: str, parts: list[tuple[SignalMatrix, float]]) -> SignalMatrix:
    num = np.zeros((c.n, c.n))
    den = np.zeros((c.n, c.n))
    for s, w in parts:
        num += np.where(s.avail, s.score, 0.0) * w
        den += s.avail * w

    def explain(i: int, j: int) -> tuple[str, list[Evidence]]:
        texts, ev = [], []
        for s, _ in parts:
            if s.avail[i, j]:
                t, e = s.explain(i, j)
                texts.append(t)
                ev.extend(e)
        return " ".join(texts), ev

    return _finish(c, name, family, num / np.maximum(den, 1e-9), den > 0, explain)


# ------------------------------------------------------------------------------------------------
# estilometría
# ------------------------------------------------------------------------------------------------
def sig_char_ngrams(c: Corpus) -> SignalMatrix:
    lens = np.array([len(f.ngram_ids) for f in c.feats])
    all_ids = np.concatenate([f.ngram_ids for f in c.feats]) if c.n else np.zeros(0, np.uint64)
    uniq, inv = np.unique(all_ids, return_inverse=True)
    rows = np.repeat(np.arange(c.n), lens)
    tf = 1.0 + np.log(np.concatenate([f.ngram_counts for f in c.feats]).astype(float))
    df = np.bincount(inv, minlength=len(uniq))
    idf = np.log((c.n + 1) / (df + 1)) + 1.0 if c.cohort else np.ones(len(uniq))
    x = sparse.csr_matrix((tf * idf[inv], (rows, inv)), shape=(c.n, max(len(uniq), 1)))
    norm = np.sqrt(np.asarray(x.multiply(x).sum(1)).ravel())
    x = (sparse.diags(1.0 / np.maximum(norm, 1e-9)) @ x).tocsr()
    score = gram(x, c.n)
    chars = np.array([sum(len(t) for t in f.texts) for f in c.feats])
    avail = np.outer(chars >= 150, chars >= 150)
    col_of = None

    def explain(i: int, j: int) -> tuple[str, list[Evidence]]:
        nonlocal col_of
        fa, fb = c.feats[i], c.feats[j]
        ev: list[Evidence] = []
        wdf = c.word_df()
        rare_cap = max(2, int(0.05 * c.n)) if c.cohort else 10**9
        shared_w = [w for w in set(fa.sets["words"]) & set(fb.sets["words"])
                    if len(w) >= 3 and wdf[w] <= rare_cap and w not in lx.FW_INDEX]
        shared_w.sort(key=lambda w: (wdf[w], -fa.sets["words"][w] * fb.sets["words"][w]))
        for w in shared_w[:3]:
            ev.append(Evidence(
                description=(f"Palabra o grafía poco común «{w}» en ambas"
                             + (f" (la usan {wdf[w]} de {c.n} cuentas)." if c.cohort else ".")),
                a=_word_snippet(fa.texts, w), b=_word_snippet(fb.texts, w)))
        prod = x[i].multiply(x[j]).tocoo()
        if prod.nnz:
            strs = ngram_strings("\n" + "\n".join(fa.texts) + "\n")
            top = prod.col[np.argsort(-prod.data)]
            shown = 0
            for col in top:
                g = strs.get(int(uniq[col]))
                if g is None or "\n" in g or g.strip() == "" or (c.cohort and df[col] > 0.3 * c.n):
                    continue
                ev.append(Evidence(
                    description=(f"Secuencia de caracteres «{g}» frecuente en ambas"
                                 + (f" y presente en {int(df[col])} de {c.n} cuentas." if c.cohort else ".")),
                    a=_snippet(fa.texts, g), b=_snippet(fb.texts, g)))
                shown += 1
                if shown >= 3:
                    break
        return (f"Similitud coseno TF-IDF de n-gramas de caracteres: {score[i, j]:.2f} "
                f"({prod.nnz} secuencias compartidas)."), ev

    return _finish(c, "stylometry.char_ngrams", "stylometry", score, avail, explain)


def sig_stylometry(c: Corpus) -> list[SignalMatrix]:
    posts_ok = c.n_authored >= 5
    rel_p = c.n_authored / (c.n_authored + 15.0)
    rel_t = c.n_tokens / (c.n_tokens + 400.0)

    def ortho_example(k: int) -> str | None:
        return f" {lx.VARIANT_DIMS[k][1]} " if k < len(lx.VARIANT_DIMS) else None

    ortho = _combine(c, "stylometry.orthography", "stylometry", [
        (habit_signal(c, "ortho", "_", "stylometry", rel_p, posts_ok,
                      "Abreviaturas, grafías y tildes", min_dims=1, example=ortho_example), 0.6),
        (set_signal(c, "misspell", "_", "stylometry", "grafía no estándar", min_total=2), 0.4),
    ])
    emoji = _combine(c, "stylometry.emoji", "stylometry", [
        (habit_signal(c, "emoji", "_", "stylometry", rel_p, posts_ok, "Uso de emojis"), 0.5),
        (set_signal(c, "emoji", "_", "stylometry", "emoji/emoticón"), 0.5),
    ])
    return [
        sig_char_ngrams(c),
        habit_signal(c, "fw", "stylometry.function_words", "stylometry", rel_t, c.n_tokens >= 60,
                     "Palabras función", min_dims=10, fast=True),
        habit_signal(c, "punct", "stylometry.punctuation", "stylometry", rel_p, posts_ok,
                     "Perfil de puntuación"),
        habit_signal(c, "caps", "stylometry.capitalization", "stylometry", rel_p, posts_ok,
                     "Uso de mayúsculas"),
        _combine(c, "stylometry.elongation", "stylometry", [
            (habit_signal(c, "elong", "_", "stylometry", rel_p, posts_ok, "Alargamientos y risas"), 0.7),
            (set_signal(c, "laugh", "_", "stylometry", "forma de risa", min_total=2), 0.3),
        ]),
        emoji,
        ortho,
        habit_signal(c, "rio", "stylometry.rioplatense", "stylometry", rel_t, c.n_tokens >= 60,
                     "Marcas dialectales (voseo, «che», «re»)"),
        habit_signal(c, "length", "stylometry.length", "stylometry", rel_p, posts_ok,
                     "Longitud de mensaje y de frase"),
    ]


# ------------------------------------------------------------------------------------------------
# temporal
# ------------------------------------------------------------------------------------------------
def _utc(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def sleep_window(hist: np.ndarray, width: int = 7) -> tuple[float, float]:
    """Centro (hora UTC) de la ventana más inactiva de `width` horas y fracción de actividad en ella."""
    sm = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))
    ext = np.concatenate([sm, sm])
    sums = np.array([ext[s : s + width].sum() for s in range(24)])
    start = int(np.argmin(sums))
    raw = np.concatenate([hist, hist])[start : start + width].sum()
    return (start + width / 2.0) % 24, float(raw / max(hist.sum(), 1.0))


def sig_temporal(c: Corpus) -> list[SignalMatrix]:
    n = c.n
    nt = np.array([len(f.times) for f in c.feats])
    hours = np.zeros((n, 24))
    week = np.zeros((n, 7))
    for i, f in enumerate(c.feats):
        if nt[i]:
            hours[i] = np.bincount((f.times % 86400) // 3600, minlength=24)
            week[i] = np.bincount((f.times // 86400 + 3) % 7, minlength=7)
    out: list[SignalMatrix] = []

    # histograma horario
    hs = 0.5 * hours + 0.25 * (np.roll(hours, 1, 1) + np.roll(hours, -1, 1)) + 0.02
    hp = np.sqrt(hs / hs.sum(1, keepdims=True))
    bc_h = hp @ hp.T

    def ex_hours(i: int, j: int) -> tuple[str, list[Evidence]]:
        def peak(k: int) -> str:
            top = sorted(np.argsort(-hours[k])[:3])
            return ", ".join(f"{h:02d} h" for h in top)
        return (f"Coincidencia del histograma por hora (UTC): {bc_h[i, j]:.2f}.",
                [Evidence(description="Horas UTC de mayor actividad de cada cuenta.",
                          a=peak(i), b=peak(j))])

    out.append(_finish(c, "temporal.hourly", "temporal", (bc_h - 0.4) / 0.6,
                       np.outer(nt >= 10, nt >= 10), ex_hours))

    # día de la semana
    wp = np.sqrt((week + 0.5) / (week + 0.5).sum(1, keepdims=True))
    bc_w = wp @ wp.T
    days = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

    def ex_week(i: int, j: int) -> tuple[str, list[Evidence]]:
        def dist(k: int) -> str:
            p = week[k] / max(week[k].sum(), 1)
            return " ".join(f"{d} {100 * v:.0f}%" for d, v in zip(days, p))
        return (f"Coincidencia de la distribución por día de semana: {bc_w[i, j]:.2f}.",
                [Evidence(description="Reparto de publicaciones por día (UTC).", a=dist(i), b=dist(j))])

    out.append(_finish(c, "temporal.weekday", "temporal", (bc_w - 0.7) / 0.3,
                       np.outer(nt >= 14, nt >= 14), ex_week))

    # ventana de sueño y huso inferido
    center = np.full(n, np.nan)
    for i in range(n):
        if nt[i] >= 25:
            ctr, frac = sleep_window(hours[i])
            if frac <= 0.12:
                center[i] = ctr
    okc = ~np.isnan(center)
    c0 = np.where(okc, center, 0.0)
    d = np.abs(c0[:, None] - c0[None, :])
    d = np.minimum(d, 24 - d)

    def ex_sleep(i: int, j: int) -> tuple[str, list[Evidence]]:
        def desc(k: int) -> str:
            s = (center[k] - 3.5) % 24
            off = int(round(((4.0 - center[k] + 12) % 24) - 12))
            return f"inactiva {int(s):02d}–{int((s + 7) % 24):02d} h UTC (huso inferido ≈ UTC{off:+d})"
        return (f"Las ventanas de inactividad difieren en {d[i, j]:.1f} h.",
                [Evidence(description="Ventana diaria de 7 h con menos actividad (sueño probable).",
                          a=desc(i), b=desc(j))])

    out.append(_finish(c, "temporal.sleep_window", "temporal", 1 - d / 6.0, np.outer(okc, okc), ex_sleep))

    # co-actividad: horas de reloj compartidas y colisiones en ventanas de 3 minutos
    has = nt > 0
    if has.sum() >= 2:
        allh = np.concatenate([f.times // 3600 for f in c.feats])
        allf = np.concatenate([f.times // 180 for f in c.feats])
        rows = np.repeat(np.arange(n), nt)
        _, hi = np.unique(allh, return_inverse=True)
        _, fi = np.unique(allf, return_inverse=True)
        ch = sparse.csr_matrix((np.ones(len(rows)), (rows, hi)), shape=(n, hi.max() + 1))
        ch.sum_duplicates()
        cf = sparse.csr_matrix((np.ones(len(rows)), (rows, fi)), shape=(n, fi.max() + 1))
        cf.sum_duplicates()
        bh = (ch > 0).astype(float)
        shared_hours = (bh @ bh.T).toarray()
        lam = (ch @ ch.T).toarray() * (180.0 / 3600.0)
        coll = (cf @ cf.T).toarray()
        d0 = np.array([f.times[0] // 86400 if len(f.times) else 0 for f in c.feats], float)
        d1 = np.array([f.times[-1] // 86400 if len(f.times) else -1 for f in c.feats], float)
        span = np.maximum(d1 - d0 + 1, 1)
        overlap = np.maximum(np.minimum(d1[:, None], d1[None, :]) - np.maximum(d0[:, None], d0[None, :]) + 1, 0)
        p = np.zeros((n, 24))
        for i, f in enumerate(c.feats):
            if nt[i]:
                p[i] = np.bincount((np.unique(f.times // 3600) % 24).astype(int), minlength=24) / span[i]
        expected = (p @ p.T) * overlap
        zs = (shared_hours - expected) / np.sqrt(expected + 1.0)
        enough = np.outer(nt >= 15, nt >= 15) & (overlap >= 7)

        def ex_sync(i: int, j: int) -> tuple[str, list[Evidence]]:
            fa, fb = c.feats[i], c.feats[j]
            ha, hb = Counter((fa.times // 3600).tolist()), Counter((fb.times // 3600).tolist())
            common = sorted(set(ha) & set(hb), key=lambda h: -(ha[h] * hb[h]))[:3]
            ev = [Evidence(description=f"Ráfaga en la misma hora: {_utc(h * 3600)} UTC.",
                           a=f"{ha[h]} publicaciones", b=f"{hb[h]} publicaciones") for h in common]
            return (f"Coincidieron en {int(shared_hours[i, j])} horas de reloj; por azar, según sus "
                    f"horarios habituales, se esperaban {expected[i, j]:.1f}."), ev

        out.append(_finish(c, "temporal.sync_bursts", "temporal", np.tanh(np.maximum(zs, 0) / 4.0),
                           enough, ex_sync))

        alt = 1.0 - poisson.cdf(coll, np.maximum(lam, 1e-9))

        def ex_alt(i: int, j: int) -> tuple[str, list[Evidence]]:
            return (f"Compartieron {int(shared_hours[i, j])} horas de actividad pero publicaron en la "
                    f"misma ventana de 3 minutos {int(coll[i, j])} veces; si fueran operadores "
                    f"independientes se esperaban {lam[i, j]:.1f}. Una sola persona no suele "
                    "publicar desde dos cuentas a la vez."), []

        out.append(_finish(c, "temporal.alternation", "temporal", alt, enough & (lam >= 2.0), ex_alt))
    return out


# ------------------------------------------------------------------------------------------------
# conducta y red
# ------------------------------------------------------------------------------------------------
def sig_behavior(c: Corpus) -> list[SignalMatrix]:
    rel = c.n_posts / (c.n_posts + 15.0)
    return [
        set_signal(c, "clients", "behavior.client", "behavior", "cliente/dispositivo", min_total=3),
        set_signal(c, "hashtags", "behavior.hashtags", "behavior", "hashtag"),
        set_signal(c, "domains", "behavior.domains", "behavior", "dominio enlazado"),
        set_signal(c, "targets", "behavior.targets", "behavior", "destinatario de respuestas/menciones"),
        habit_signal(c, "mix", "behavior.post_mix", "behavior", rel, c.n_posts >= 8,
                     "Proporción original/respuesta/repost"),
    ]


def sig_network(c: Corpus) -> list[SignalMatrix]:
    out = [
        set_signal(c, "following", "network.following", "network", "cuenta seguida", min_total=5,
                   jaccard=True),
        set_signal(c, "followers", "network.followers", "network", "seguidor", min_total=5,
                   jaccard=True),
    ]
    index = {(f.platform, f.handle_norm): i for i, f in enumerate(c.feats)}
    m = np.zeros((c.n, c.n))
    tot = np.zeros(c.n)
    for i, f in enumerate(c.feats):
        for h, cnt in f.sets["targets"].items():
            tot[i] += cnt
            j = index.get((f.platform, h))
            if j is not None and j != i:
                m[i, j] += cnt
    fr = np.sqrt(m / np.maximum(tot, 1.0)[:, None])
    score = 1.0 - (1.0 - fr) * (1.0 - fr.T)

    def explain(i: int, j: int) -> tuple[str, list[Evidence]]:
        ev = []
        if m[i, j] or m[j, i]:
            ev.append(Evidence(description="Menciones o respuestas dirigidas a la otra cuenta.",
                               a=f"{int(m[i, j])} de {int(tot[i])}", b=f"{int(m[j, i])} de {int(tot[j])}"))
        return ("Interacción directa entre las cuentas (menciones mutuas): "
                + ("sí." if ev else "ninguna observada.")), ev

    out.append(_finish(c, "network.mutual_mentions", "network", score,
                       c.same_platform & np.outer(tot >= 3, tot >= 3), explain))
    return out


# ------------------------------------------------------------------------------------------------
# perfil
# ------------------------------------------------------------------------------------------------
def _tfidf_texts(c: Corpus, texts: list[str]) -> np.ndarray:
    ids, rows = [], []
    for i, t in enumerate(texts):
        u = np.unique(ngram_hashes(" " + t.lower() + " ")) if t else np.zeros(0, np.uint64)
        ids.append(u)
        rows.append(np.full(len(u), i))
    uniq, inv = np.unique(np.concatenate(ids), return_inverse=True)
    df = np.bincount(inv, minlength=len(uniq))
    idf = np.log((c.n + 1) / (df + 1)) + 1.0 if c.cohort else np.ones(len(uniq))
    x = sparse.csr_matrix((idf[inv], (np.concatenate(rows), inv)), shape=(c.n, max(len(uniq), 1)))
    norm = np.sqrt(np.asarray(x.multiply(x).sum(1)).ravel())
    return gram((sparse.diags(1.0 / np.maximum(norm, 1e-9)) @ x).tocsr(), c.n)


def sig_profile(c: Corpus) -> list[SignalMatrix]:
    n = c.n
    f = c.feats
    out: list[SignalMatrix] = []

    # handle
    rad = [x.handle_radical for x in f]
    alt = [x.handle_alt for x in f]
    hs = process.cdist(rad, rad, scorer=fuzz.ratio, dtype=np.float32)
    hs = np.maximum(hs, process.cdist(alt, alt, scorer=fuzz.ratio, dtype=np.float32))
    cross = process.cdist(rad, alt, scorer=fuzz.ratio, dtype=np.float32)
    hs = np.maximum(hs, np.maximum(cross, cross.T)) / 100.0
    by_len = sorted(range(n), key=lambda i: len(rad[i]))
    for a_pos, i in enumerate(by_len):
        if len(rad[i]) < 4:
            continue
        for j in by_len[a_pos + 1:]:
            if rad[i] != rad[j] and rad[i] in rad[j]:
                hs[i, j] = hs[j, i] = max(hs[i, j], 0.85)
    ok = np.array([len(r) >= 3 for r in rad])

    def ex_handle(i: int, j: int) -> tuple[str, list[Evidence]]:
        notes = []
        if {rad[i], alt[i]} & {rad[j], alt[j]} and f[i].handle_norm != f[j].handle_norm:
            notes.append("mismo radical con distinto sufijo, números o separadores")
        elif rad[i] in rad[j] or rad[j] in rad[i]:
            notes.append("un radical contiene al otro")
        if any(ch in "013457@$8" for ch in f[i].handle_norm + f[j].handle_norm) and hs[i, j] > 0.8:
            notes.append("posible variante leetspeak")
        return (f"Similitud de handle {hs[i, j]:.2f}" + (": " + "; ".join(notes) if notes else "") + ".",
                [Evidence(description="Handles y radical normalizado (sin números ni leetspeak).",
                          a=f"{f[i].handle} → {rad[i]}", b=f"{f[j].handle} → {rad[j]}")])

    out.append(_finish(c, "profile.handle", "profile", hs, np.outer(ok, ok), ex_handle))

    # nombre visible
    dn = [x.display_norm for x in f]
    ds = process.cdist(dn, dn, scorer=fuzz.token_sort_ratio, dtype=np.float32) / 100.0
    okd = np.array([len(x) >= 3 for x in dn])
    out.append(_finish(c, "profile.display_name", "profile", ds, np.outer(okd, okd), lambda i, j: (
        f"Similitud de nombre visible {ds[i, j]:.2f}.",
        [Evidence(description="Nombres visibles.", a=dn[i], b=dn[j])])))

    # bio
    bios = [x.bio for x in f]
    okb = np.array([len(b) >= 12 for b in bios])
    if okb.sum() >= 2:
        bs = _tfidf_texts(c, [b if o else "" for b, o in zip(bios, okb)])
        out.append(_finish(c, "profile.bio", "profile", bs, np.outer(okb, okb), lambda i, j: (
            f"Similitud de biografía {bs[i, j]:.2f} (trigramas ponderados por rareza).",
            [Evidence(description="Biografías.", a=bios[i][:160], b=bios[j][:160])])))

    # fecha de creación
    cd = np.array([x.created_days for x in f])
    okc = ~np.isnan(cd)
    c0 = np.where(okc, cd, 0.0)
    dd = np.abs(c0[:, None] - c0[None, :])
    out.append(_finish(c, "profile.creation_date", "profile", np.exp(-dd / 30.0), np.outer(okc, okc),
                       lambda i, j: (
        f"Cuentas creadas con {dd[i, j]:.0f} días de diferencia.",
        [Evidence(description="Fecha de creación (UTC).", a=_utc(cd[i] * 86400)[:10],
                  b=_utc(cd[j] * 86400)[:10])])))

    # avatar (hash perceptual)
    counts = Counter(x.phash for x in f if x.phash is not None)
    default_cut = max(3, int(0.02 * n) + 1)
    oka = np.array([x.phash is not None and counts[x.phash] < default_cut for x in f])
    idx = np.where(oka)[0]
    if len(idx) >= 2:
        av = np.zeros((n, n))
        ham = np.zeros((n, n))
        same_bits = np.zeros((n, n), dtype=bool)
        if all(f[i].phash_bits <= 64 for i in idx):
            h = np.array([f[i].phash for i in idx], dtype=np.uint64)
            dist = np.bitwise_count(h[:, None] ^ h[None, :]).astype(float)
            bits = np.array([f[i].phash_bits for i in idx], float)
            ham[np.ix_(idx, idx)] = dist
            same_bits[np.ix_(idx, idx)] = bits[:, None] == bits[None, :]
            av[np.ix_(idx, idx)] = 1.0 - dist / (0.35 * bits[:, None])
        else:
            for a_pos, i in enumerate(idx):
                for j in idx[a_pos + 1:]:
                    if f[i].phash_bits == f[j].phash_bits:
                        dist = (f[i].phash ^ f[j].phash).bit_count()
                        ham[i, j] = ham[j, i] = dist
                        same_bits[i, j] = same_bits[j, i] = True
                        av[i, j] = av[j, i] = 1.0 - dist / (0.35 * f[i].phash_bits)
        out.append(_finish(c, "profile.avatar", "profile", av, same_bits, lambda i, j: (
            f"Distancia de Hamming entre hashes perceptuales de avatar: {int(ham[i, j])} de "
            f"{f[i].phash_bits} bits" + (" (imagen prácticamente idéntica)." if ham[i, j] <= 6 else "."),
            [Evidence(description="pHash del avatar.", a=f"{f[i].phash:0{f[i].phash_bits // 4}x}",
                      b=f"{f[j].phash:0{f[j].phash_bits // 4}x}")])))
    return out


def compute_all(c: Corpus) -> list[SignalMatrix]:
    return sig_stylometry(c) + sig_temporal(c) + sig_behavior(c) + sig_network(c) + sig_profile(c)
