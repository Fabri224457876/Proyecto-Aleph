"""Entrena el modelo sobre el grafo de un caso y propone los vínculos que faltan."""

from collections import defaultdict

import numpy as np

from .features import build_features, fold
from .model import Jepa
from .schemas import (
    Hypothesis,
    HypothesisEvidence,
    Quality,
    TlonInput,
    TlonParams,
    TlonReport,
)

MIN_NODES = 4
MIN_EDGES = 4
REVERSE = "~"  # prefijo del sentido inverso de una relación


def _contexts(X: np.ndarray, neighbors: dict[int, set[int]], pairs: list[tuple[int, int]]) -> np.ndarray:
    """s_u para cada par (u, v): u junto con sus vecinos, sin v (lo que hay que predecir)."""
    rows = []
    for u, v in pairs:
        others = [n for n in neighbors[u] if n != v]
        rows.append(0.5 * X[u] + 0.5 * X[others].mean(0) if others else X[u])
    return np.vstack(rows) if rows else np.zeros((0, X.shape[1]))


def _cos_rows(P: np.ndarray, T: np.ndarray) -> np.ndarray:
    Pn = P / (np.linalg.norm(P, axis=1, keepdims=True) + 1e-8)
    Tn = T / (np.linalg.norm(T, axis=1, keepdims=True) + 1e-8)
    return Pn @ Tn.T


def _shared_attributes(a, b) -> list[str]:
    shared = []
    for key, value in a.props.items():
        other = b.props.get(key)
        comparable = all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in (value, other))
        if comparable and str(value).strip() and fold(str(value)) == fold(str(other)):
            shared.append(f"{key} = {value}")
    tokens_a = {t for t in fold(a.label).replace("@", " ").replace(".", " ").split() if len(t) >= 4}
    tokens_b = {t for t in fold(b.label).replace("@", " ").replace(".", " ").split() if len(t) >= 4}
    shared += [f"comparten «{t}» en el nombre" for t in sorted(tokens_a & tokens_b)[:3]]
    return shared[:6]


def propose(data: TlonInput, params: TlonParams | None = None) -> TlonReport:
    params = params or TlonParams()
    nodes = data.nodes
    index = {n.id: i for i, n in enumerate(nodes)}
    edges = [e for e in data.edges if e.src in index and e.dst in index and e.src != e.dst]
    base = TlonReport(status="ok", nodes=len(nodes), edges=len(edges), params=params.model_dump())
    if len(nodes) < MIN_NODES or len(edges) < MIN_EDGES:
        base.status = "datos_insuficientes"
        base.message = (f"Hacen falta al menos {MIN_NODES} entidades y {MIN_EDGES} vínculos para aprender "
                        f"algo del caso; hay {len(nodes)} y {len(edges)}.")
        return base

    rng = np.random.default_rng(params.seed)
    X, feature_source = build_features(nodes, edges, data.vectors)

    # Relaciones dirigidas: r y su inverso ~r, así el modelo predice en los dos sentidos.
    rel_names = sorted({e.type for e in edges})
    rel_index = {name: i for i, name in enumerate(rel_names)}
    rel_index.update({REVERSE + name: i + len(rel_names) for i, name in enumerate(rel_names)})
    rel_label = {v: k for k, v in rel_index.items()}

    order = rng.permutation(len(edges))
    n_hold = round(params.holdout * len(edges)) if len(edges) >= 10 else 0
    hold_ids, train_ids = set(order[:n_hold].tolist()), order[n_hold:].tolist()

    def triples(ids):
        out = []
        for k in ids:
            e = edges[k]
            u, v, w = index[e.src], index[e.dst], (1.0 if e.confirmed else 0.5)
            out.append((u, v, rel_index[e.type], w))
            out.append((v, u, rel_index[REVERSE + e.type], w))
        return out

    train = triples(train_ids)
    neighbors: dict[int, set[int]] = defaultdict(set)
    for u, v, _, _ in train:
        neighbors[u].add(v)

    model = Jepa(X.shape[1], len(rel_index), dim=params.dim, hidden=params.hidden, ema=params.ema,
                 lr=params.lr, seed=params.seed)
    S = _contexts(X, neighbors, [(u, v) for u, v, _, _ in train])
    rel = np.array([r for _, _, r, _ in train])
    weights = np.array([w for _, _, _, w in train])
    targets_idx = np.array([v for _, v, _, _ in train])
    loss = 0.0
    for _ in range(params.epochs):
        loss = model.step(S, rel, X, targets_idx, weights)

    T_all = model.targets(X)

    # Tipos compatibles: qué tipo de destino se vio para cada (tipo de origen, relación).
    allowed: dict[tuple[str, int], set[str]] = defaultdict(set)
    for u, v, r, _ in train:
        allowed[(nodes[u].type, r)].add(nodes[v].type)

    # -- calidad sobre los vínculos ocultos --
    hold = triples(sorted(hold_ids))
    mrr = hits = base_hits = None
    if hold:
        P = model.predict(_contexts(X, neighbors, [(u, v) for u, v, _, _ in hold]), np.array([r for _, _, r, _ in hold]))
        scores = _cos_rows(P, T_all)
        ranks, rand = [], []
        for row, (u, v, r, _) in zip(scores, hold):
            ok_types = allowed.get((nodes[u].type, r)) or {n.type for n in nodes}
            cand = [j for j in range(len(nodes)) if j != u and j not in neighbors[u] and nodes[j].type in ok_types]
            if v not in cand:
                cand.append(v)
            better = sum(1 for j in cand if row[j] > row[v])
            ranks.append(better + 1)
            rand.append(min(5, len(cand)) / len(cand))
        mrr = float(np.mean([1 / k for k in ranks]))
        hits = float(np.mean([k <= 5 for k in ranks]))
        base_hits = float(np.mean(rand))

    # -- distribución de puntajes de los vínculos reales, por tipo de relación, para el percentil --
    P_train = model.predict(S, rel)
    real = np.sum((P_train / (np.linalg.norm(P_train, axis=1, keepdims=True) + 1e-8))
                  * (T_all[targets_idx] / (np.linalg.norm(T_all[targets_idx], axis=1, keepdims=True) + 1e-8)), axis=1)
    all_scores = np.sort(real)
    by_rel: dict[int, np.ndarray] = {}
    for r in set(rel.tolist()):
        scores_r = np.sort(real[rel == r])
        if len(scores_r) >= 3:  # con menos no hay distribución: se compara contra todos
            by_rel[r] = scores_r

    def percentile_of(score: float, r: int) -> float:
        ref = by_rel.get(r, all_scores)
        return float(np.searchsorted(ref, score) / max(1, len(ref)))

    # -- hipótesis: lo que el modelo predice fuerte y no existe --
    full_neighbors: dict[int, set[int]] = defaultdict(set)
    for e in edges:
        full_neighbors[index[e.src]].add(index[e.dst])
        full_neighbors[index[e.dst]].add(index[e.src])
    # Cardinalidad aprendida: si casi todo origen tiene un solo vínculo r (cada correo pertenece a
    # un dominio), no se propone un segundo r para quien ya tiene uno.
    out_count: dict[tuple[int, int], int] = defaultdict(int)
    for e in edges:
        out_count[(index[e.src], rel_index[e.type])] += 1
        out_count[(index[e.dst], rel_index[REVERSE + e.type])] += 1
    per_rel: dict[int, list[int]] = defaultdict(list)
    for (_, r), k in out_count.items():
        per_rel[r].append(k)
    functional = {r: len(ks) >= 3 and sum(k == 1 for k in ks) / len(ks) >= 0.9 for r, ks in per_rel.items()}

    def inverse(r: int) -> int:
        return r + len(rel_names) if r < len(rel_names) else r - len(rel_names)

    def breaks_cardinality(u: int, r: int, v: int) -> bool:
        return (functional.get(r, False) and out_count[(u, r)] >= 1) or (
            functional.get(inverse(r), False) and out_count[(v, inverse(r))] >= 1)

    blocked = {frozenset((index[a], index[b])) for a, b in data.blocked if a in index and b in index}
    queries = [(u, r) for (utype, r) in allowed for u in range(len(nodes))
               if nodes[u].type == utype and full_neighbors[u]]
    candidates: dict[tuple[int, int], tuple[float, int]] = {}
    if queries:
        P = model.predict(_contexts(X, full_neighbors, [(u, -1) for u, _ in queries]),
                          np.array([r for _, r in queries]))
        scores = _cos_rows(P, T_all)
        for row, (u, r) in zip(scores, queries):
            ok_types = allowed[(nodes[u].type, r)]
            for j in np.argsort(-row)[:10]:
                j = int(j)
                if j == u or j in full_neighbors[u] or nodes[j].type not in ok_types:
                    continue
                if breaks_cardinality(u, r, j) or frozenset((u, j)) in blocked:
                    continue
                key = (min(u, j), max(u, j))
                if key not in candidates or row[j] > candidates[key][0]:
                    candidates[key] = (float(row[j]), r if u < j else -1 - r)

    hypotheses: list[Hypothesis] = []
    ranked = []
    for (a, b), (score, r_code) in candidates.items():
        r = r_code if r_code >= 0 else -1 - r_code
        ranked.append((percentile_of(score, r), score, a, b, r_code))
    for percentile, score, a, b, r_code in sorted(ranked, key=lambda x: (-x[0], -x[1])):
        if percentile < params.min_percentile:
            continue
        # Se presenta en el sentido directo de la relación.
        r = r_code if r_code >= 0 else -1 - r_code
        u, v = (a, b) if r_code >= 0 else (b, a)
        name = rel_label[r]
        if name.startswith(REVERSE):
            name, u, v = name[len(REVERSE):], v, u
        sim = _cos_rows(T_all[list(full_neighbors[u])], T_all[[v]])[:, 0] if full_neighbors[u] else np.array([])
        nbrs = list(full_neighbors[u])
        similar = [nodes[nbrs[k]].label for k in np.argsort(-sim)[:2] if sim[k] > 0.5]
        common = [nodes[k].label for k in sorted(full_neighbors[u] & full_neighbors[v])][:5]
        shared = _shared_attributes(nodes[u], nodes[v])
        parts = [(f"El modelo del caso espera un vínculo «{name}» entre {nodes[u].label} y {nodes[v].label}"
                  f" (más fuerte que el {percentile:.0%} de los vínculos reales de ese tipo).")]
        if common:
            parts.append(f"Tienen {len(common)} vecino(s) en común: {', '.join(common[:3])}.")
        if similar:
            parts.append(f"{nodes[u].label} ya se vincula con entidades parecidas: {', '.join(similar)}.")
        if shared:
            parts.append(f"Comparten: {'; '.join(shared[:3])}.")
        hypotheses.append(Hypothesis(
            src=nodes[u].id, dst=nodes[v].id, src_label=nodes[u].label, dst_label=nodes[v].label,
            type=name, score=round(score, 4), percentile=round(percentile, 4),
            evidence=HypothesisEvidence(similar_neighbors=similar, common_neighbors=common,
                                        shared_attributes=shared, explanation=" ".join(parts)),
        ))
        if len(hypotheses) >= params.max_hypotheses:
            break

    base.quality = Quality(
        holdout_edges=len(hold) // 2, mrr=None if mrr is None else round(mrr, 4),
        hits_at_5=None if hits is None else round(hits, 4),
        baseline_hits_at_5=None if base_hits is None else round(base_hits, 4),
        final_loss=round(float(loss), 5), feature_source=feature_source,
    )
    base.hypotheses = hypotheses
    if not hypotheses:
        base.message = "El modelo no encontró vínculos faltantes con fuerza suficiente."
    return base
