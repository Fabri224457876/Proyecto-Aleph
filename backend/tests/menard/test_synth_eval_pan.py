import json

import numpy as np

from aleph.core.schemas import AccountProfile
from aleph.menard import fusion
from aleph.menard.eval import auc_roc, eer, evaluate, precision_at_recall, precision_recall, train
from aleph.menard.pan import evaluate_pan, iter_pan, pair_profiles, text_to_profile
from aleph.menard.synth import compose, generate_world, sample_idiolect


def test_world_is_deterministic_by_seed():
    a = generate_world(seed=11, n_personas=12)
    b = generate_world(seed=11, n_personas=12)
    c = generate_world(seed=12, n_personas=12)
    dump = lambda w: [p.model_dump(mode="json") for p in w.profiles]  # noqa: E731
    assert dump(a) == dump(b) and a.persona_of == b.persona_of
    assert dump(a) != dump(c)


def test_world_contains_the_hard_cases(world):
    assert all(isinstance(p, AccountProfile) for p in world.profiles)
    assert len({p.key for p in world.profiles}) == len(world.profiles)
    by_persona = {}
    for p in world.profiles:
        by_persona.setdefault(world.persona_of[p.key], []).append(p)
    sizes = [len(v) for v in by_persona.values()]
    assert 1 in sizes and max(sizes) >= 3 and max(sizes) <= 4
    assert any(len({p.account.platform for p in v}) >= 2 for v in by_persona.values())
    assert any(len(v) >= 2 and len({p.account.platform for p in v}) < len(v) for v in by_persona.values())
    assert {p.account.platform for p in world.profiles} == {"twitter", "instagram", "discord"}
    assert any(m["disguised"] for m in world.meta.values())
    arch = [m["archetype"] for k, m in world.meta.items()]
    assert len(set(arch)) < len(by_persona)  # hay personas con estilo clonado
    posts = [len(p.posts) for p in world.profiles]
    assert sum(n <= 12 for n in posts) >= 3 and max(posts) > 80
    for p in world.profiles:
        if p.account.platform == "discord":
            assert not any(q.hashtags or q.client for q in p.posts)
        assert all(q.created_at.tzinfo is not None for q in p.posts)
    langs = {q.lang for p in world.profiles for q in p.posts}
    assert langs == {"es", "en"}
    # ningún dominio real: todo cae bajo el TLD reservado .example
    assert all(u.split("/")[2].endswith(".example") for p in world.profiles for q in p.posts for u in q.urls)


def test_idiolect_is_visible_in_text():
    rng = np.random.default_rng(3)
    idi = sample_idiolect(rng, "rio")
    idi.p.update(acc_omit=1.0, laugh=1.0, laugh_end=1.0, upper_start=0.0, caps_post=0.0, emoji_p=0.0,
                 emoticon_p=0.0, caps_word=0.0, elong=0.0)
    idi.laughs = ["jajsjaj"]
    idi.abbr = {"que": ("q", 1.0)}
    w = {(t, k): np.ones(20) for t in ("futbol",) for k in ("S", "O", "S_en", "O_en")}
    texts = [compose(rng, idi, "futbol", {k: v[: len(v)] for k, v in _item_w().items()}, False)
             for _ in range(40)]
    assert w and all("jajsjaj" in t for t in texts)
    joined = " ".join(texts)
    assert " que " not in joined and " q " in joined
    assert not any(ch in joined for ch in "áéíóú")


def _item_w():
    from aleph.menard import synth_data as sd
    return {("futbol", k): np.ones(len(sd.TOPICS["futbol"][k])) for k in ("S", "O", "S_en", "O_en")}


def test_metrics_on_known_values():
    y = np.array([1, 1, 1, 0, 0, 0, 0], bool)
    s = np.array([0.9, 0.8, 0.3, 0.7, 0.2, 0.1, 0.05])
    assert abs(auc_roc(y, s) - 11 / 12) < 1e-12
    assert precision_recall(y, s, 0.5) == (2 / 3, 2 / 3)
    assert precision_at_recall(y, s, 1.0)[0] == 3 / 4
    assert precision_at_recall(y, s, 0.5)[0] == 1.0
    assert auc_roc(y, y.astype(float)) == 1.0 and eer(y, y.astype(float)) == 0.0
    assert abs(eer(y, 1.0 - y) - 1.0) < 1e-12
    assert 0.2 < eer(y, s) < 0.4
    assert np.isnan(auc_roc(np.zeros(3, bool), np.ones(3)))


def test_fit_recovers_a_separable_signal_and_respects_sign_constraint():
    rng = np.random.default_rng(0)
    n = 4000
    y = rng.random(n) < 0.1
    good = np.clip(np.where(y, 0.8, 0.2) + rng.normal(0, 0.1, n), 0, 1)
    inverted = np.clip(np.where(y, 0.2, 0.6) + rng.normal(0, 0.2, n), 0, 1)
    avail = rng.random(n) < 0.7
    ones, zeros = np.ones(n, bool), np.zeros(n)
    x = {"stylometry.good": (good, avail, zeros, ~ones), "profile.inverted": (inverted, ones, zeros, ~ones)}
    ctx = {k: zeros for k in fusion.CONTEXT}
    m = fusion.fit(x, ctx, y, use_z=False, l2=1e-4)
    w_good, _, _ = m.signals["stylometry.good"]
    w_inv, _, _ = m.signals["profile.inverted"]
    assert w_good > 3 and w_inv == 0.0  # parecerse nunca resta
    sigs = [("stylometry.good", "stylometry", good, avail), ("profile.inverted", "profile", inverted, ones)]
    p = 1 / (1 + np.exp(-fusion.logit(m, sigs, {}, ctx)))
    assert auc_roc(y, p) > 0.9 and abs(p.mean() - y.mean()) < 0.01  # calibrado al prior


def test_cohort_z_highlights_outlying_pair():
    rng = np.random.default_rng(1)
    n = 30
    s = rng.uniform(0.4, 0.6, (n, n))
    s = (s + s.T) / 2
    s[3, 7] = s[7, 3] = 0.95
    avail = ~np.eye(n, dtype=bool)
    z, za = fusion.cohort_z(s, avail)
    assert za[3, 7] and z[3, 7] == z.max() and z[3, 7] > 0.5
    z2, za2 = fusion.cohort_z(s[:5, :5], avail[:5, :5])
    assert not za2.any() and not z2.any()


def test_train_and_evaluate_end_to_end_small(tmp_path):
    path = tmp_path / "weights.json"
    w = train(personas=30, seeds=(901, 902), path=path, verbose=False)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved["models"]) == {"cohort", "pairwise"} and "punct" in saved["habits"]
    assert w.cohort.trained and 0 < w.train_prior < 0.1
    assert all(v[0] >= 0 and v[2] >= 0 for v in w.cohort.signals.values())
    res = evaluate(personas=30, seeds=(903,), weights=w, timing=False)
    assert res["overall"]["positives"] > 0 and res["overall"]["auc"] > 0.85
    assert set(res["ablation"]) == {"stylometry", "temporal", "behavior", "network", "profile"}
    assert len(res["by_posts"]) == 4 and len(res["by_platform"]) == 2
    from aleph.menard.eval import render_markdown
    md = render_markdown(res)
    assert "AUC-ROC" in md and "Dónde falla" in md


def test_pan_jsonl_loader_and_evaluation(tmp_path):
    rng = np.random.default_rng(4)
    authors = [sample_idiolect(rng, "rio") for _ in range(12)]
    w = _item_w()

    def doc(idi):
        return " ".join(compose(rng, idi, "futbol", w, False) for _ in range(25))

    pairs, truth = [], []
    for k in range(24):
        a = authors[k % 12]
        same = k % 2 == 0
        b = a if same else authors[(k + 5) % 12]
        pairs.append({"id": f"p{k}", "fandoms": ["x", "y"], "pair": [doc(a), doc(b)]})
        truth.append({"id": f"p{k}", "same": same, "authors": ["1", "1" if same else "2"]})
    pp, tp = tmp_path / "pairs.jsonl", tmp_path / "truth.jsonl"
    pp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in pairs) + "\n", encoding="utf-8")
    tp.write_text("\n".join(json.dumps(r) for r in truth) + "\n", encoding="utf-8")
    loaded = list(iter_pan(pp, tp))
    assert len(loaded) == 24 and loaded[0].same is True and loaded[1].same is False
    assert loaded[0].meta["fandoms"] == ["x", "y"]
    assert list(iter_pan(pp))[0].same is None
    a, b = pair_profiles(loaded[0])
    assert a.key != b.key and len(a.posts) > 3 and all(len(q.text) <= 280 for q in a.posts)
    res = evaluate_pan(pp, tp)
    assert res["pairs"] == 24 and res["metrics"]["auc"] > 0.8
    assert evaluate_pan(pp, tp, limit=4)["pairs"] == 4


def test_pan_folder_loader(tmp_path):
    for name, label in (("EN001", "Y"), ("EN002", "N")):
        d = tmp_path / name
        d.mkdir()
        (d / "known01.txt").write_text("Texto conocido uno. Otra frase.", encoding="utf-8")
        (d / "known02.txt").write_text("Texto conocido dos.", encoding="utf-8")
        (d / "unknown.txt").write_text("Texto en disputa.", encoding="utf-8")
    (tmp_path / "truth.txt").write_text("EN001 Y\nEN002 N\n", encoding="utf-8")
    loaded = list(iter_pan(tmp_path))
    assert [(p.id, p.same) for p in loaded] == [("EN001", True), ("EN002", False)]
    assert "conocido dos" in loaded[0].texts[0] and loaded[0].meta["known_docs"] == 2


def test_text_to_profile_chunks_long_sentences():
    p = text_to_profile("a" * 700 + ". Corta. " + "b" * 100, "doc", max_chars=280)
    assert [len(q.text) for q in p.posts][:2] == [280, 280] and all(len(q.text) <= 280 for q in p.posts)
    assert p.account.platform == "pan" and text_to_profile("", "vacio").posts == []
