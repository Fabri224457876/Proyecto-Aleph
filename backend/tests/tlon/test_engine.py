"""TLÖN: gradientes exactos, recuperación de un vínculo faltante, cardinalidad y casos sin datos."""

import numpy as np

from aleph.tlon import TlonEdge, TlonInput, TlonNode, propose
from aleph.tlon.model import Jepa
from aleph.tlon.schemas import TlonParams


def groups(n: int, missing: int | None = None, vectors: bool = False) -> TlonInput:
    """n grupos sintéticos persona → cuenta → correo → dominio, con la cuenta vinculada al dominio."""
    nodes, edges, vecs = [], [], {}
    rng = np.random.default_rng(1)
    for g in range(n):
        base = g * 4
        nodes += [TlonNode(id=base + 1, type="person", label=f"Persona {g} Sintética"),
                  TlonNode(id=base + 2, type="account", label=f"@operador_{g}"),
                  TlonNode(id=base + 3, type="email", label=f"operador{g}@red{g}.test"),
                  TlonNode(id=base + 4, type="domain", label=f"red{g}.test")]
        edges += [TlonEdge(src=base + 1, dst=base + 2, type="controls"),
                  TlonEdge(src=base + 2, dst=base + 3, type="uses_email"),
                  TlonEdge(src=base + 3, dst=base + 4, type="belongs_to")]
        if g != missing:
            edges.append(TlonEdge(src=base + 2, dst=base + 4, type="shares_domain"))
        if vectors:
            topic = rng.normal(size=16)
            for k in range(1, 5):
                vecs[base + k] = (topic + 0.1 * rng.normal(size=16)).tolist()
    return TlonInput(nodes=nodes, edges=edges, vectors=vecs)


def test_gradientes_coinciden_con_diferencias_finitas():
    rng = np.random.default_rng(0)
    X, S = rng.normal(size=(9, 12)), rng.normal(size=(7, 12))
    rel, tidx, w = rng.integers(0, 3, 7), rng.integers(0, 9, 7), np.ones(7)
    model = Jepa(12, 3, dim=4, hidden=6, lr=0.0, weight_decay=0.0, ema=1.0)
    captured = {}
    model.opt.step = lambda params, grads: captured.update({k: v.copy() for k, v in grads.items()})
    model.step(S, rel, X, tidx, w)

    def loss_at() -> float:
        probe = Jepa(12, 3, dim=4, hidden=6, lr=0.0, weight_decay=0.0, ema=1.0)
        probe.p = {k: v.copy() for k, v in model.p.items()}
        probe.W_target = model.W_target.copy()
        probe.opt.step = lambda params, grads: None
        return probe.step(S, rel, X, tidx, w)

    for name in ("W", "A", "B", "E", "b1", "b2"):
        idx = tuple(int(rng.integers(0, s)) for s in model.p[name].shape)
        eps = 1e-6
        model.p[name][idx] += eps
        up = loss_at()
        model.p[name][idx] -= 2 * eps
        down = loss_at()
        model.p[name][idx] += eps
        assert abs((up - down) / (2 * eps) - captured[name][idx]) < 1e-5, name


def test_recupera_el_vinculo_que_falta():
    report = propose(groups(20, missing=3), TlonParams(min_percentile=0.3))
    assert report.status == "ok"
    top = report.hypotheses[0]
    assert (top.src, top.dst, top.type) == (3 * 4 + 2, 3 * 4 + 4, "shares_domain")
    assert top.evidence.explanation and top.percentile >= 0.5


def test_aprende_algo_mejor_que_el_azar():
    q = propose(groups(20), TlonParams(min_percentile=0.99)).quality
    assert q is not None and q.holdout_edges > 0
    assert q.hits_at_5 > q.baseline_hits_at_5


def test_respeta_la_cardinalidad_aprendida():
    """Cada persona controla una sola cuenta: no propone que controle otra."""
    report = propose(groups(20, missing=3), TlonParams(min_percentile=0.0, max_hypotheses=50))
    assert all(h.type != "controls" for h in report.hypotheses)
    assert all(h.type != "belongs_to" for h in report.hypotheses)


def test_no_propone_vinculos_que_ya_existen():
    data = groups(12, missing=5)
    existing = {frozenset((e.src, e.dst)) for e in data.edges}
    report = propose(data, TlonParams(min_percentile=0.0, max_hypotheses=50))
    assert all(frozenset((h.src, h.dst)) not in existing for h in report.hypotheses)


def test_usa_embeddings_cuando_estan():
    report = propose(groups(10, vectors=True), TlonParams(min_percentile=0.0))
    assert report.quality.feature_source == "embeddings"


def test_es_determinista_con_la_misma_semilla():
    a = propose(groups(10, missing=2), TlonParams(min_percentile=0.0))
    b = propose(groups(10, missing=2), TlonParams(min_percentile=0.0))
    assert [(h.src, h.dst, h.score) for h in a.hypotheses] == [(h.src, h.dst, h.score) for h in b.hypotheses]


def test_datos_insuficientes():
    data = TlonInput(nodes=[TlonNode(id=1, type="person", label="A"), TlonNode(id=2, type="person", label="B")],
                     edges=[TlonEdge(src=1, dst=2, type="knows")])
    report = propose(data)
    assert report.status == "datos_insuficientes" and "al menos" in report.message
    assert report.hypotheses == []
