"""Evaluación reproducible de MENARD sobre el dataset sintético.

    python -m aleph.menard.eval                 # evalúa con los pesos del paquete
    python -m aleph.menard.eval --train         # reentrena la fusión y guarda weights.json
    python -m aleph.menard.eval --write-md      # además escribe EVALUATION.md

Entrenamiento y prueba usan mundos generados con semillas distintas: ninguna persona de prueba
aparece en entrenamiento (la separación es por persona, no por par).
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import rankdata

from . import fusion
from .engine import DEFAULT_MIN_POSTS, DEFAULT_THRESHOLD, FAMILIES, analyze, analyze_matrix, build_clusters
from .features import HABIT_DIMS, extract
from .synth import SynthWorld, generate_world

TRAIN_SEEDS = (101, 102, 103, 104, 105)
TEST_SEEDS = (201, 202, 203, 204)
PERSONAS_PER_WORLD = 150
POST_BINS = ((5, 12), (13, 30), (31, 80), (81, 10**9))
MD_PATH = Path(__file__).with_name("EVALUATION.md")
L2_GRID = (1e-4, 3e-5, 1e-5, 3e-6, 1e-6)
NEG_FRACTION = 0.2


# ------------------------------------------------------------------------------------------------
# métricas
# ------------------------------------------------------------------------------------------------
def auc_roc(y: np.ndarray, s: np.ndarray) -> float:
    y = y.astype(bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(s)
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def eer(y: np.ndarray, s: np.ndarray) -> float:
    y = y.astype(bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(-s, kind="stable")
    ys = y[order]
    tp = np.cumsum(ys)
    fp = np.cumsum(~ys)
    last = np.r_[s[order][1:] != s[order][:-1], True]  # un punto por umbral distinto
    fnr = np.r_[1.0, 1 - tp[last] / n1]
    fpr = np.r_[0.0, fp[last] / n0]
    k = int(np.argmin(np.abs(fnr - fpr)))
    return float((fnr[k] + fpr[k]) / 2)


def precision_recall(y: np.ndarray, s: np.ndarray, thr: float) -> tuple[float, float]:
    y = y.astype(bool)
    pred = s >= thr
    tp = int((pred & y).sum())
    prec = tp / pred.sum() if pred.sum() else float("nan")
    rec = tp / y.sum() if y.sum() else float("nan")
    return float(prec), float(rec)


def precision_at_recall(y: np.ndarray, s: np.ndarray, recall: float) -> tuple[float, float]:
    """Precisión (y umbral) en el punto de operación que alcanza al menos `recall`."""
    y = y.astype(bool)
    pos = np.sort(s[y])[::-1]
    if len(pos) == 0:
        return float("nan"), float("nan")
    thr = pos[min(len(pos) - 1, int(np.ceil(recall * len(pos))) - 1)]
    return precision_recall(y, s, thr)[0], float(thr)


def summarize(y: np.ndarray, s: np.ndarray, thr: float = DEFAULT_THRESHOLD) -> dict[str, float]:
    p, r = precision_recall(y, s, thr)
    return {
        "pairs": int(len(y)), "positives": int(y.sum()), "auc": auc_roc(y, s), "eer": eer(y, s),
        "precision": p, "recall": r,
        "p_at_r50": precision_at_recall(y, s, 0.5)[0], "p_at_r80": precision_at_recall(y, s, 0.8)[0],
        "p_at_r90": precision_at_recall(y, s, 0.9)[0],
        "brier": float(np.mean((s - y) ** 2)) if len(y) else float("nan"),
    }


# ------------------------------------------------------------------------------------------------
# datos
# ------------------------------------------------------------------------------------------------
@dataclass
class WorldData:
    world: SynthWorld
    feats: dict


def load_world(seed: int, personas: int) -> WorldData:
    w = generate_world(seed, personas)
    return WorldData(w, {p.key: extract(p) for p in w.profiles if len(p.posts) >= DEFAULT_MIN_POSTS})


def reference_stats(data: list[WorldData]) -> dict[str, Any]:
    """Media y desvío de cada hábito por plataforma: referencia cuando no hay cohorte."""
    feats = [f for d in data for f in d.feats.values()]
    out: dict[str, Any] = {}
    for group in HABIT_DIMS:
        out[group] = {}
        for plat in ["_all"] + sorted({f.platform for f in feats}):
            m = np.vstack([f.habits[group] for f in feats if plat in ("_all", f.platform)])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # columnas sin datos
                mu = np.nan_to_num(np.nanmean(m, 0))
                sd = np.nan_to_num(np.nanstd(m, 0), nan=1.0)
            out[group][plat] = {"mean": [round(float(x), 5) for x in mu],
                                "std": [round(float(max(x, 1e-3)), 5) for x in sd]}
    return out


def train(personas: int = PERSONAS_PER_WORLD, seeds: tuple[int, ...] = TRAIN_SEEDS,
          path: Path | None = None, verbose: bool = True) -> fusion.Weights:
    data = [load_world(s, personas) for s in seeds]
    ref = reference_stats(data)
    base = fusion.Weights(ref={"habits": ref})
    models = {}
    chosen: dict[str, float] = {}
    prior = 0.0
    for mode in ("cohort", "pairwise"):
        cols: dict[str, list[list[np.ndarray]]] = {}
        ctxs: dict[str, list[np.ndarray]] = {k: [] for k in fusion.CONTEXT}
        ys = []
        wid = []
        for wi, d in enumerate(data):
            an = analyze_matrix(d.world.profiles, use_cohort=(mode == "cohort"), weights=base,
                                features=d.feats)
            iu = np.triu_indices(an.corpus.n, 1)
            ys.append(d.world.labels(an.keys)[iu])
            wid.append(np.full(len(iu[0]), wi))
            for k in fusion.CONTEXT:
                ctxs[k].append(an.ctx[k][iu])
            for s in an.signals:
                z, za = an.zfeat.get(s.name, (np.zeros_like(s.score), np.zeros_like(s.avail)))
                cols.setdefault(s.name, []).append([s.score[iu], s.avail[iu], z[iu], za[iu]])
        y = np.concatenate(ys)
        total = len(y)
        x_sig = {}
        for name, parts in cols.items():
            arrs = [np.concatenate([p[i] for p in parts]) for i in range(4)]
            if len(arrs[0]) == total:  # la señal existió en todos los mundos
                x_sig[name] = tuple(arrs)
        ctx = {k: np.concatenate(v) for k, v in ctxs.items()}
        world_id = np.concatenate(wid)
        # Submuestreo de negativos con peso compensatorio: mismo prior, ajuste mucho más rápido.
        rs = np.random.default_rng(0)
        keep = y | (rs.random(total) < NEG_FRACTION)
        sw = np.where(y, 1.0, 1.0 / NEG_FRACTION)

        def run(mask: np.ndarray, l2: float) -> fusion.FusionModel:
            return fusion.fit({k: tuple(a[mask] for a in v) for k, v in x_sig.items()},
                              {k: v[mask] for k, v in ctx.items()}, y[mask],
                              use_z=(mode == "cohort"), l2=l2, sample_weight=sw[mask])

        # La regularización se elige validando sobre el último mundo de entrenamiento.
        val = world_id == len(data) - 1
        best = (np.inf, L2_GRID[0])
        if len(data) > 1:
            for l2 in L2_GRID:
                m = run(keep & ~val, l2)
                sigs = [(k, k.split(".")[0], v[0][val], v[1][val]) for k, v in x_sig.items()]
                zf = {k: (v[2][val], v[3][val]) for k, v in x_sig.items()}
                lg = fusion.logit(m, sigs, zf, {k: v[val] for k, v in ctx.items()})
                nll = float(np.mean(np.logaddexp(0.0, lg) - y[val] * lg))
                if verbose:
                    print(f"[train] modo {mode}: l2={l2:g} → log-loss de validación {nll:.5f}")
                best = min(best, (nll, l2))
        models[mode] = run(keep, best[1])
        chosen[mode] = best[1]
        prior = float(y.mean())
        if verbose:
            print(f"[train] modo {mode}: {total} pares, {int(y.sum())} positivos, "
                  f"{len(x_sig)} señales, l2={best[1]:g}")
    meta = {"dataset": "aleph.menard.synth", "train_seeds": list(seeds), "personas_per_world": personas,
            "l2": chosen, "negative_fraction": NEG_FRACTION,
            "note": "Pesos ajustados sobre datos sintéticos; recalibrar con datos reales etiquetados."}
    fusion.save_weights(models["cohort"], models["pairwise"], ref, prior, meta, path)
    return fusion.load_weights(path)


# ------------------------------------------------------------------------------------------------
# evaluación
# ------------------------------------------------------------------------------------------------
def evaluate(personas: int = PERSONAS_PER_WORLD, seeds: tuple[int, ...] = TEST_SEEDS,
             weights: fusion.Weights | None = None, timing: bool = True) -> dict[str, Any]:
    w = weights or fusion.load_weights()
    acc: dict[str, list[np.ndarray]] = {}
    per_world = []
    cluster_stats = np.zeros(3)

    def add(name: str, arr: np.ndarray) -> None:
        acc.setdefault(name, []).append(arr)

    n_accounts = 0
    for seed in seeds:
        d = load_world(seed, personas)
        world = d.world
        an = analyze_matrix(world.profiles, weights=w, features=d.feats)
        n = an.corpus.n
        n_accounts += n
        iu = np.triu_indices(n, 1)
        y = world.labels(an.keys)[iu]
        s = an.scores[iu]
        add("y", y)
        add("score", s)
        per_world.append(auc_roc(y, s))
        posts = an.corpus.n_posts
        add("min_posts", np.minimum(posts[:, None], posts[None, :])[iu])
        add("same_platform", an.corpus.same_platform[iu])
        dis = np.array([world.meta[k]["disguised"] for k in an.keys])
        add("disguised", (dis[:, None] | dis[None, :])[iu])
        arch = np.array([world.meta[k]["archetype"] for k in an.keys])
        add("same_archetype", (arch[:, None] == arch[None, :])[iu])
        for fam in FAMILIES[:-1]:
            add("only_" + fam, an.fused([fam])[iu])
            add("without_" + fam, an.fused([f for f in FAMILIES if f != fam])[iu])
        for sig in an.signals:
            add("sig:" + sig.name, np.where(sig.avail[iu], sig.score[iu], np.nan))
        pw = analyze_matrix(world.profiles, weights=w, features=d.feats, use_cohort=False)
        add("pairwise", pw.scores[iu])
        # clusters: precisión/recall de co-pertenencia
        clusters = build_clusters(an.keys, an.scores, DEFAULT_THRESHOLD)
        idx = {k: i for i, k in enumerate(an.keys)}
        co = np.zeros((n, n), bool)
        for c in clusters:
            m = [idx[k] for k in c.members]
            co[np.ix_(m, m)] = True
        cluster_stats += [(co[iu] & y).sum(), co[iu].sum(), y.sum()]
    a = {k: np.concatenate(v) for k, v in acc.items()}
    y, s = a["y"], a["score"]
    res: dict[str, Any] = {
        "seeds": list(seeds), "personas_per_world": personas, "accounts": n_accounts,
        "weights": w.source, "threshold": DEFAULT_THRESHOLD,
        "overall": summarize(y, s), "auc_per_world": per_world,
        "pairwise_mode": summarize(y, a["pairwise"]),
        "by_posts": {}, "by_platform": {}, "ablation": {}, "signals": {},
    }
    for lo, hi in POST_BINS:
        m = (a["min_posts"] >= lo) & (a["min_posts"] <= hi)
        res["by_posts"][f"{lo}–{hi}" if hi < 10**8 else f"{lo}+"] = summarize(y[m], s[m])
    for name, m in (("misma plataforma", a["same_platform"]), ("distinta plataforma", ~a["same_platform"])):
        res["by_platform"][name] = summarize(y[m], s[m])
    neg = ~y
    hard_pos = y & a["disguised"]
    easy_pos = y & ~a["disguised"]
    res["disguise"] = {
        "con cuenta disfrazada": summarize(y[hard_pos | neg], s[hard_pos | neg]),
        "sin disfraz": summarize(y[easy_pos | neg], s[easy_pos | neg]),
    }
    clones = neg & a["same_archetype"]
    others = neg & ~a["same_archetype"]
    res["archetype"] = {
        "negativos de estilo clonado": {
            "pairs": int(clones.sum()),
            "fpr": float((s[clones] >= DEFAULT_THRESHOLD).mean()) if clones.any() else float("nan"),
            "auc_vs_pos": auc_roc(y[y | clones], s[y | clones]),
        },
        "resto de negativos": {
            "pairs": int(others.sum()),
            "fpr": float((s[others] >= DEFAULT_THRESHOLD).mean()),
            "auc_vs_pos": auc_roc(y[y | others], s[y | others]),
        },
    }
    for fam in FAMILIES[:-1]:
        res["ablation"][fam] = {"only": summarize(y, a["only_" + fam]),
                                "without": summarize(y, a["without_" + fam])}
    for k, v in a.items():
        if k.startswith("sig:"):
            ok = ~np.isnan(v)
            res["signals"][k[4:]] = {"coverage": float(ok.mean()), "auc": auc_roc(y[ok], v[ok]),
                                     "positives": int(y[ok].sum())}
    tp, pred, pos = cluster_stats
    res["clusters"] = {"precision": float(tp / pred) if pred else float("nan"),
                       "recall": float(tp / pos) if pos else float("nan")}
    if timing:
        res["timing"] = measure_timing()
    return res


def measure_timing(target_accounts: int = 200) -> dict[str, float]:
    w = generate_world(999, 118)
    profiles = w.profiles[:target_accounts]
    t0 = time.perf_counter()
    feats = {p.key: extract(p) for p in profiles}
    t1 = time.perf_counter()
    analyze_matrix(profiles, features=feats)
    t2 = time.perf_counter()
    rep = analyze(profiles)
    t3 = time.perf_counter()
    return {"accounts": len(profiles), "posts": sum(len(p.posts) for p in profiles),
            "features_s": t1 - t0, "signals_fusion_s": t2 - t1, "analyze_total_s": t3 - t2,
            "pairs_reported": len(rep.pairs), "clusters": len(rep.clusters)}


# ------------------------------------------------------------------------------------------------
# informe
# ------------------------------------------------------------------------------------------------
def _f(x: float) -> str:
    return "s/d" if x != x else f"{x:.3f}"


def _row(name: str, m: dict[str, float]) -> str:
    return (f"| {name} | {m['pairs']} | {m['positives']} | {_f(m['auc'])} | {_f(m['eer'])} | "
            f"{_f(m['precision'])} | {_f(m['recall'])} | {_f(m['p_at_r50'])} | {_f(m['p_at_r80'])} |")


_HEAD = ("| Subconjunto | Pares | Positivos | AUC-ROC | EER | Precisión@0.5 | Recall@0.5 | "
         "P@R=0.5 | P@R=0.8 |\n|---|---|---|---|---|---|---|---|---|")


def render_markdown(r: dict[str, Any]) -> str:
    o = r["overall"]
    out = [
        "# MENARD — evaluación", "",
        "Resultados reales del evaluador, sin retoques. Se regeneran con:", "",
        "```", "cd backend",
        "../.venv/Scripts/python -m aleph.menard.eval --train --write-md   # reentrena y evalúa",
        "../.venv/Scripts/python -m aleph.menard.eval --write-md           # solo evalúa", "```", "",
        "## Qué se mide y qué no", "",
        f"- Dataset **sintético** (`aleph.menard.synth`): {len(r['seeds'])} mundos de prueba "
        f"(semillas {r['seeds']}), {r['personas_per_world']} personas ficticias cada uno, "
        f"{r['accounts']} cuentas elegibles en total. Los pares solo se forman dentro de un mundo.",
        f"- La fusión se entrenó con otros mundos (semillas {list(TRAIN_SEEDS)}): ninguna persona de "
        "prueba aparece en entrenamiento. Entrenamiento y prueba sí comparten el *proceso generador* "
        "(mismas plantillas y vocabulario), así que estos números son un techo optimista.",
        "- **No hay todavía evaluación con datos reales.** El texto sintético sale de plantillas y "
        "es mucho más regular que el de personas reales; los pesos y la calibración hay que "
        "revalidarlos con datos etiquetados (ver cargador PAN en `aleph.menard.pan`).",
        f"- Umbral por defecto: {r['threshold']}. El puntaje está calibrado al prior del dataset "
        f"({o['positives']} positivos sobre {o['pairs']} pares, {100 * o['positives'] / o['pairs']:.2f} %).",
        "", "## Resultado global", "", _HEAD, _row("Todos los pares", o), "",
        f"- Precisión con recall fijo: P@R=0.5 = {_f(o['p_at_r50'])}, P@R=0.8 = {_f(o['p_at_r80'])}, "
        f"P@R=0.9 = {_f(o['p_at_r90'])}.",
        f"- Brier: {o['brier']:.5f}. AUC por mundo: "
        + ", ".join(_f(x) for x in r["auc_per_world"]) + ".",
        f"- Clusters (co-pertenencia de pares, umbral {r['threshold']}): precisión "
        f"{_f(r['clusters']['precision'])}, recall {_f(r['clusters']['recall'])}.",
        "", "## Por cantidad de publicaciones (la cuenta con menos posts del par)", "", _HEAD,
    ]
    out += [_row(k, v) for k, v in r["by_posts"].items()]
    out += ["", "## Misma plataforma vs. distinta plataforma", "", _HEAD]
    out += [_row(k, v) for k, v in r["by_platform"].items()]
    out += ["", "## Casos difíciles", "",
            "Positivos restringidos al subconjunto indicado, contra todos los negativos.", "", _HEAD]
    out += [_row(k, v) for k, v in r["disguise"].items()]
    out += ["", "Negativos de estilo clonado (personas distintas generadas a partir del mismo "
            "arquetipo de idiolecto):", "",
            "| Negativos | Pares | Falsos positivos @0.5 | AUC contra los positivos |", "|---|---|---|---|"]
    out += [f"| {k} | {v['pairs']} | {100 * v['fpr']:.2f} % | {_f(v['auc_vs_pos'])} |"
            for k, v in r["archetype"].items()]
    out += ["", "## Ablación por familia de señales", "",
            "Sin reentrenar: se usan los mismos pesos y se quitan señales. Por eso la precisión y el "
            "recall a umbral fijo de las filas «solo» no están calibrados; mirar AUC y EER.", "",
            "| Familia | AUC solo esa familia | EER solo | AUC sin esa familia | EER sin | "
            "Recall@0.5 sin | Precisión@0.5 sin |", "|---|---|---|---|---|---|---|"]
    for fam, v in r["ablation"].items():
        out.append(f"| {fam} | {_f(v['only']['auc'])} | {_f(v['only']['eer'])} | "
                   f"{_f(v['without']['auc'])} | {_f(v['without']['eer'])} | "
                   f"{_f(v['without']['recall'])} | {_f(v['without']['precision'])} |")
    out += ["", "## Señales individuales", "",
            "AUC de cada señal por separado, solo sobre los pares donde está disponible.", "",
            "| Señal | Cobertura | Positivos cubiertos | AUC |", "|---|---|---|---|"]
    for name, v in r["signals"].items():
        out.append(f"| `{name}` | {100 * v['coverage']:.1f} % | {v['positives']} | {_f(v['auc'])} |")
    p = r["pairwise_mode"]
    out += ["", "## Modo sin cohorte (`compare(a, b)` sin `background`)", "",
            "Sin conjunto de referencia no hay IDF ni normalización por cohorte; se usan estadísticas "
            "de referencia del paquete y un segundo juego de pesos.", "", _HEAD,
            _row("Todos los pares", p)]
    if "timing" in r:
        t = r["timing"]
        out += ["", "## Rendimiento", "",
                f"Medido en la máquina de desarrollo, un solo proceso, sobre {t['accounts']} cuentas "
                f"sintéticas ({t['posts']} publicaciones, {t['accounts'] * (t['accounts'] - 1) // 2} pares):", "",
                f"- extracción de rasgos por cuenta: {t['features_s']:.2f} s",
                f"- señales + fusión (todas las matrices N×N, con rasgos ya extraídos): {t['signals_fusion_s']:.2f} s",
                f"- `analyze()` completo (rasgos, señales, fusión, evidencia de {t['pairs_reported']} "
                f"pares y {t['clusters']} clusters): **{t['analyze_total_s']:.2f} s**"]
    out += ["", "## Dónde falla", "", "Lecturas de las tablas de arriba, sin maquillaje:", ""]
    out += failure_notes(r)
    out += ["", "## Pendiente", "",
            "- Evaluar con datasets públicos de verificación de autoría (formato PAN): el cargador "
            "está en `aleph.menard.pan`, no se descargó ni corrió nada.",
            "- Ablación con reentrenamiento por familia y curvas de calibración.",
            "- Señal neuronal (familia `neural`): solo está la interfaz.", ""]
    return "\n".join(out)


def failure_notes(r: dict[str, Any]) -> list[str]:
    o = r["overall"]
    notes = [f"- Al umbral 0.5 el motor recupera {100 * o['recall']:.0f} % de los pares del mismo operador "
             f"con precisión {100 * o['precision']:.0f} %: el resto de los positivos queda por debajo "
             "del umbral."]
    bp = r["by_posts"]
    first, lastk = next(iter(bp)), list(bp)[-1]
    notes.append(f"- Con pocas publicaciones ({first}) el recall cae a {_f(bp[first]['recall'])} "
                 f"(AUC {_f(bp[first]['auc'])}) contra {_f(bp[lastk]['recall'])} "
                 f"(AUC {_f(bp[lastk]['auc'])}) con {lastk}.")
    pl = r["by_platform"]
    notes.append(f"- Multiplataforma: recall {_f(pl['distinta plataforma']['recall'])} y AUC "
                 f"{_f(pl['distinta plataforma']['auc'])} entre plataformas distintas, contra "
                 f"{_f(pl['misma plataforma']['recall'])} y {_f(pl['misma plataforma']['auc'])} "
                 "en la misma plataforma.")
    dg = r["disguise"]
    notes.append(f"- Cuentas disfrazadas: recall {_f(dg['con cuenta disfrazada']['recall'])} "
                 f"(AUC {_f(dg['con cuenta disfrazada']['auc'])}) contra "
                 f"{_f(dg['sin disfraz']['recall'])} (AUC {_f(dg['sin disfraz']['auc'])}) sin disfraz.")
    ar = r["archetype"]
    notes.append(f"- Estilos clonados: {100 * ar['negativos de estilo clonado']['fpr']:.2f} % de falsos "
                 f"positivos entre personas distintas con idiolecto casi igual, contra "
                 f"{100 * ar['resto de negativos']['fpr']:.3f} % en el resto.")
    pw = r["pairwise_mode"]
    notes.append(f"- Sin cohorte (`compare` de un par aislado) el AUC es {_f(pw['auc'])} y el recall "
                 f"al umbral {_f(pw['recall'])}.")
    weak = [k for k, v in r["signals"].items() if v["auc"] == v["auc"] and v["auc"] < 0.65]
    if weak:
        notes.append("- Señales que por sí solas aportan poco en este dataset (AUC < 0.65): "
                     + ", ".join(f"`{k}`" for k in weak) + ".")
    rare = [k for k, v in r["signals"].items() if v["coverage"] < 0.02]
    if rare:
        notes.append("- Señales casi nunca disponibles (< 2 % de los pares), por lo que su peso está "
                     "poco respaldado: " + ", ".join(f"`{k}`" for k in rare) + ".")
    return notes


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Evaluación de MENARD sobre el dataset sintético.")
    ap.add_argument("--train", action="store_true", help="reentrena la fusión y guarda weights.json")
    ap.add_argument("--write-md", action="store_true", help="escribe EVALUATION.md")
    ap.add_argument("--personas", type=int, default=PERSONAS_PER_WORLD)
    ap.add_argument("--no-timing", action="store_true")
    args = ap.parse_args(argv)
    if args.train:
        train(args.personas)
    res = evaluate(args.personas, timing=not args.no_timing)
    md = render_markdown(res)
    print(md)
    if args.write_md:
        MD_PATH.write_text(md, encoding="utf-8")
        print(f"\n[escrito {MD_PATH}]")


if __name__ == "__main__":
    main()
