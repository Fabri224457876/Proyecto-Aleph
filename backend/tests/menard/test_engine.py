import time

import numpy as np
import pytest

from aleph.core.schemas import MenardReport, PairResult
from aleph.menard import (
    NeuralScores,
    analyze,
    analyze_matrix,
    build_clusters,
    compare,
    register_neural_signal,
    unregister_neural_signal,
)
from aleph.menard import fusion
from aleph.menard.eval import auc_roc

from .conftest import make_profile

FORBIDDEN = ("son la misma persona", "es la misma persona", "identidad confirmada")


@pytest.fixture(scope="module")
def report(world):
    return analyze(world.profiles)


def test_report_structure(world, report):
    assert isinstance(report, MenardReport)
    assert report.params["mode"] == "cohort" and report.params["calibrated"] is True
    assert report.pairs, "el mundo sintético tiene multicuentas: debería haber hipótesis"
    scores = [p.score for p in report.pairs]
    assert scores == sorted(scores, reverse=True)
    keys = {p.key for p in world.profiles}
    for p in report.pairs:
        assert 0.0 <= p.score <= 1.0 and p.a in keys and p.b in keys and p.a != p.b
        assert p.confidence in ("baja", "media", "alta")
        assert len({s.name for s in p.signals}) == len(p.signals)
        for s in p.signals:
            assert 0.0 <= s.score <= 1.0 and s.explanation
            if not s.available:
                assert s.score == 0.0 and not s.evidence


def test_summaries_are_hypotheses_not_identity_claims(report):
    for p in report.pairs:
        low = p.summary.lower()
        assert "hipótesis" in low and "analista" in low
        assert not any(f in low for f in FORBIDDEN)
    for c in report.clusters:
        assert c.summary.startswith("Hipótesis de mismo operador")


def test_accounts_with_few_posts_are_skipped(world):
    few = [make_profile("pocos1", ["hola che"] * 3), make_profile("pocos2", ["hola che"] * 4)]
    rep = analyze(world.profiles[:30] + few, evidence=False)
    assert set(rep.skipped) == {p.key for p in few} | {p.key for p in world.profiles[:30] if len(p.posts) < 5}
    assert all("pocas publicaciones" in v for v in rep.skipped.values())
    listed = {k for p in rep.pairs for k in (p.a, p.b)}
    assert not listed & set(rep.skipped)
    assert analyze(few, min_posts=3).skipped == {}


def test_same_operator_pairs_rank_above_the_rest(world, analysis):
    iu = np.triu_indices(analysis.corpus.n, 1)
    y = world.labels(analysis.keys)[iu]
    s = analysis.scores[iu]
    assert y.sum() >= 10
    assert auc_roc(y, s) > 0.93
    assert s[y].mean() > 10 * s[~y].mean()


def test_clusters_group_accounts_of_one_persona(world, report):
    assert report.clusters
    pure = sum(len({world.persona_of[m] for m in c.members}) == 1 for c in report.clusters)
    assert pure / len(report.clusters) >= 0.7
    for c in report.clusters:
        assert len(c.members) >= 2 and 0.0 <= c.cohesion <= 1.0


def test_build_clusters_does_not_chain_weak_links():
    keys = list("abcd")
    s = np.zeros((4, 4))
    for i, j, v in ((0, 1, 0.95), (2, 3, 0.9), (1, 2, 0.55)):  # un solo puente débil entre dos grupos
        s[i, j] = s[j, i] = v
    clusters = build_clusters(keys, s, threshold=0.5)
    assert sorted(sorted(c.members) for c in clusters) == [["a", "b"], ["c", "d"]]


def test_confidence_reflects_amount_of_evidence(world, analysis, report):
    for p in report.pairs:
        if p.confidence == "alta":
            i, j = analysis.keys.index(p.a), analysis.keys.index(p.b)
            assert min(len(analysis.profiles[i].posts), len(analysis.profiles[j].posts)) >= 30
            assert p.score >= 0.85
    # Dos cuentas calcadas pero con muy pocos posts: puntaje alto, confianza nunca "alta".
    texts = ["xq nadie me avisó q hoy jugaban?? jajsjaj re mal 😭😭", "che q onda con el finde, sale birra??",
             "nose q hacer con la compu, anda re lenta jajsjaj", "posta q no entiendo nada 😭",
             "q día de locos che, me voy a dormir", "xq siempre me pasa lo mismo?? re injusto"]
    a = make_profile("lucho_87", texts, avatar_phash="9f3c5a7710e2b4d6")
    b = make_profile("lucho.ok", texts[::-1], avatar_phash="9f3c5a7710e2b4d6")
    assert compare(a, b, background=world.profiles).confidence != "alta"
    assert compare(a, b).confidence != "alta"


def test_compare_with_and_without_background(world):
    by = {}
    for p in world.profiles:
        if len(p.posts) >= 40:
            by.setdefault(world.persona_of[p.key], []).append(p)
    groups = [g for g in by.values() if len(g) >= 2]
    singles = [g[0] for g in by.values() if len(g) == 1]
    assert groups and singles
    same = [(g[0], g[1]) for g in groups][:3]
    diff = [(g[0], s) for g, s in zip(groups, singles)][:3]
    for kwargs in ({}, {"background": world.profiles[:40]}):
        s_same = np.mean([compare(a, b, **kwargs).score for a, b in same])
        s_diff = np.mean([compare(a, b, **kwargs).score for a, b in diff])
        assert s_same > s_diff + 0.3, kwargs
    r = compare(*same[0])
    assert isinstance(r, PairResult) and "sin conjunto de referencia" in r.summary
    assert r.confidence != "alta"
    with pytest.raises(ValueError):
        compare(same[0][0], same[0][0])


def test_score_is_computable_from_any_subset_of_signals(world, analysis):
    iu = np.triu_indices(analysis.corpus.n, 1)
    y = world.labels(analysis.keys)[iu]
    for fams in (["stylometry"], ["temporal", "profile"], ["network"], []):
        s = analysis.fused(fams)[iu]
        assert np.isfinite(s).all() and s.min() >= 0 and s.max() <= 1
    assert auc_roc(y, analysis.fused(["stylometry"])[iu]) > 0.85
    rep = analyze(world.profiles[:40], families=["stylometry"], evidence=False)
    assert rep.params["families"] == ["stylometry"]
    assert all(s.startswith("stylometry.") for s in rep.params["signals"])


def test_cross_platform_pairs_degrade_gracefully():
    texts = ["che no puedo creer lo del partido, re mal el arbitraje jajsjaj",
             "posta q el técnico no entiende nada, encima perdimos de local",
             "bueno me voy a dormir q mañana hay q laburar 😴",
             "alguien vio el resumen?? xq yo no entiendo nada jajsjaj",
             "igual la defensa está re floja, ponele q mejora",
             "qué quilombo se armó con la dirigencia che 😴"] * 4
    tw = make_profile("tincho_cai", texts)
    for p in tw.posts:
        p.hashtags, p.client = ["futbol"], "Twitter for Android"
    dc = make_profile("tinchoCAI", [t.split(",")[0] for t in texts], platform="discord", start=None)
    r = compare(tw, dc)
    sig = {s.name: s for s in r.signals}
    assert not sig["behavior.hashtags"].available and not sig["behavior.client"].available
    assert not sig["temporal.hourly"].available and not sig["network.mutual_mentions"].available
    assert sig["stylometry.char_ngrams"].available and sig["profile.handle"].score > 0.9
    assert 0.0 <= r.score <= 1.0 and "discord:tinchocai" in r.summary


def test_degenerate_inputs():
    assert analyze([]).pairs == []
    one = make_profile("solo", ["hola"] * 10)
    rep = analyze([one])
    assert rep.pairs == [] and rep.clusters == [] and rep.params["accounts_analyzed"] == 1
    dup = analyze([one, one, make_profile("otro", ["chau"] * 10)])
    assert "duplicada" in dup.skipped[one.key]
    empty = make_profile("vacio", [""] * 6, start=None)
    assert 0.0 <= compare(empty, make_profile("lleno", ["texto normal de prueba"] * 6)).score <= 1.0


def test_deterministic(world):
    a = analyze_matrix(world.profiles[:50])
    b = analyze_matrix(world.profiles[:50])
    assert np.array_equal(a.scores, b.scores)


def test_prior_shifts_scores_monotonically(world):
    base = analyze_matrix(world.profiles[:60])
    high = analyze_matrix(world.profiles[:60], prior=0.2)
    iu = np.triu_indices(base.corpus.n, 1)
    assert (high.scores[iu] >= base.scores[iu] - 1e-12).all()
    assert high.scores[iu].mean() > base.scores[iu].mean()


def test_neural_extension_point(world):
    profiles = world.profiles[:30]
    calls = []

    def fake_embeddings(ps):
        calls.append(len(ps))
        n = len(ps)
        score = np.full((n, n), 0.1)
        score[0, 1] = score[1, 0] = 0.99
        return NeuralScores(score=score, explanation="Similitud de embeddings (simulada).")

    def broken(ps):
        return NeuralScores(score=np.zeros((2, 2)))

    base = analyze_matrix(profiles)
    name = register_neural_signal(fake_embeddings, name="fake")
    register_neural_signal(broken, name="broken")
    try:
        an = analyze_matrix(profiles)
        sig = {s.name: s for s in an.signals}
        assert name == "neural.fake" and sig[name].family == "neural"
        assert "neural.broken" not in sig and any("neural.broken" in w for w in an.warnings)
        assert calls == [an.corpus.n]
        assert an.scores[0, 1] > base.scores[0, 1] and an.scores[2, 3] < base.scores[2, 3]
        res = [s for s in an.pair(0, 1).signals if s.name == name][0]
        assert res.available and res.score == 0.99 and "simulada" in res.explanation
    finally:
        unregister_neural_signal("fake")
        unregister_neural_signal("broken")
    assert not any(s.family == "neural" for s in analyze_matrix(profiles).signals)


def test_fallback_weights_when_file_is_missing_or_corrupt(tmp_path, world):
    bad = tmp_path / "weights.json"
    bad.write_text("{no es json", encoding="utf-8")
    for path in (bad, tmp_path / "no_existe.json"):
        w = fusion.load_weights(path)
        assert w.source == "fallback" and not w.cohort.trained
        an = analyze_matrix(world.profiles[:40], weights=w)
        iu = np.triu_indices(an.corpus.n, 1)
        assert np.isfinite(an.scores).all()
        y = world.labels(an.keys)[iu]
        if y.any():
            assert auc_roc(y, an.scores[iu]) > 0.8
        assert "sin calibrar" in an.pair(0, 1).summary


def test_analysis_time_is_reasonable(world):
    t0 = time.perf_counter()
    analyze(world.profiles)
    assert time.perf_counter() - t0 < 20  # ~75 cuentas; en la máquina de desarrollo tarda ~1-2 s
