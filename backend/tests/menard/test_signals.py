from datetime import datetime, timedelta, timezone

import numpy as np

from aleph.menard.features import extract
from aleph.menard.signals import Corpus, compute_all, sleep_window

from .conftest import T0, make_profile


def _signals(profiles):
    c = Corpus([extract(p) for p in profiles])
    return {s.name: s for s in compute_all(c)}, c


def test_all_families_present_and_scores_bounded(analysis):
    fams = {s.family for s in analysis.signals}
    assert fams == {"stylometry", "temporal", "behavior", "network", "profile"}
    for s in analysis.signals:
        assert s.score.shape == (analysis.corpus.n,) * 2
        assert s.score.min() >= 0.0 and s.score.max() <= 1.0, s.name
        assert not s.avail.diagonal().any()
        assert np.array_equal(s.avail, s.avail.T), s.name
        assert np.allclose(s.score, s.score.T, atol=1e-5), s.name
        assert (s.score[~s.avail] == 0).all()


def test_missing_data_is_unavailable_not_neutral():
    a = make_profile("alfa", ["un texto cualquiera sin nada raro"] * 6, start=None)
    b = make_profile("beta", ["otro texto distinto sin fechas tampoco"] * 6, start=None)
    sig, _ = _signals([a, b])
    for name in ("temporal.hourly", "temporal.weekday", "temporal.sleep_window", "behavior.hashtags",
                 "behavior.domains", "behavior.client", "network.following", "profile.creation_date"):
        assert not sig[name].avail[0, 1], name
    assert "profile.avatar" not in sig and "profile.bio" not in sig


def test_handle_signal_radical_numbers_and_leet():
    ps = [make_profile(h, ["hola"] * 5) for h in ("nico_dev87", "n1c0dev", "nicodev_2001", "marianela")]
    sig, _ = _signals(ps)
    s = sig["profile.handle"].score
    assert s[0, 1] > 0.95 and s[0, 2] > 0.95 and s[0, 3] < 0.5
    text, ev = sig["profile.handle"].explain(0, 1)
    assert "radical" in text and ev[0].a.endswith("nicodev")


def test_avatar_hamming_and_default_avatar_ignored():
    base = 0x9F3C5A7710E2B4D6

    def mk(handle, phash):
        return make_profile(handle, ["hola"] * 5, avatar_phash=f"{phash:016x}")

    ps = [mk("a1", base), mk("a2", base ^ 0b101), mk("a3", 0x0123456789ABCDEF)]
    ps += [mk(f"d{i}", 0xFFFF0000FFFF0000) for i in range(4)]  # avatar por defecto repetido
    sig, _ = _signals(ps)
    av = sig["profile.avatar"]
    assert av.avail[0, 1] and av.score[0, 1] > 0.9
    assert av.score[0, 2] < 0.2
    assert not av.avail[3, 4]
    assert "2 de 64" in av.explain(0, 1)[0]


def test_creation_date_closeness():
    d = datetime(2024, 5, 1, tzinfo=timezone.utc)
    ps = [make_profile("a", ["x"] * 5, created_at_platform=d),
          make_profile("b", ["x"] * 5, created_at_platform=d + timedelta(days=2)),
          make_profile("c", ["x"] * 5, created_at_platform=d + timedelta(days=900))]
    s = _signals(ps)[0]["profile.creation_date"].score
    assert s[0, 1] > 0.9 and s[0, 2] < 0.01


def test_rare_shared_hashtags_weigh_more_than_common_ones():
    ps = []
    for i in range(24):
        p = make_profile(f"user{i}", ["algo"] * 6)
        for post in p.posts:
            post.hashtags = ["futbol"]
        ps.append(p)
    for i in (0, 1):
        for post in ps[i].posts[:4]:
            post.hashtags = ["futbol", "lunesdemate"]
    sig, c = _signals(ps)
    s = sig["behavior.hashtags"]
    assert c.cohort and s.score[0, 1] > s.score[2, 3]
    _, ev = s.explain(0, 1)
    assert "lunesdemate" in ev[0].description and "2 de 24" in ev[0].description


def test_following_jaccard_and_mutual_mentions():
    a = make_profile("ana", ["hola"] * 6, following_handles=[f"amigo{i}" for i in range(20)])
    b = make_profile("bea", ["hola"] * 6, following_handles=[f"amigo{i}" for i in range(10, 30)])
    c = make_profile("caro", ["hola"] * 6, following_handles=[f"otro{i}" for i in range(20)])
    for p, target in ((a, "bea"), (b, "ana"), (c, "nadie")):
        for post in p.posts:
            post.mentions = [target]
    sig, _ = _signals([a, b, c])
    f = sig["network.following"].score
    assert abs(f[0, 1] - 10 / 30 * np.tanh(10 / 1.5)) < 1e-6 and f[0, 2] == 0
    m = sig["network.mutual_mentions"]
    assert m.score[0, 1] == 1.0 and m.score[0, 2] == 0.0 and m.avail[0, 2]


def test_sleep_window_and_timezone_inference():
    hist = np.zeros(24)
    hist[[13, 14, 15, 20, 21, 22, 23, 0, 1, 2]] = 5.0  # activa de 10 a 23 h en UTC-3
    center, frac = sleep_window(hist)
    assert frac == 0.0 and 6.0 <= center <= 9.5


def test_sync_bursts_and_alternation():
    # A y B publican en las mismas horas pero nunca en la misma ventana de 3 minutos; C va por su lado
    def times(offset_s, hours):
        return [T0 + timedelta(hours=h, seconds=offset_s + 400 * k) for h in hours for k in range(6)]

    days_ab = [0, 1, 3, 7, 8, 15, 20, 22, 29, 33, 41, 50]
    days_c = [2, 4, 5, 9, 12, 17, 21, 25, 30, 36, 44, 49]
    hours_ab = [24 * d for d in days_ab]
    pa = make_profile("a", ["x"] * 72)
    pb = make_profile("b", ["x"] * 72)
    pc = make_profile("c", ["x"] * 72)
    for p, ts in ((pa, times(0, hours_ab)), (pb, times(200, hours_ab)),
                  (pc, times(0, [24 * d for d in days_c]))):
        for post, t in zip(p.posts, ts):
            post.created_at = t
    sig, _ = _signals([pa, pb, pc])
    sync, alt = sig["temporal.sync_bursts"], sig["temporal.alternation"]
    assert sync.avail[0, 1] and sync.score[0, 1] > 0.5 and sync.score[0, 2] < 0.1
    assert alt.avail[0, 1] and alt.score[0, 1] > 0.9
    assert not alt.avail[0, 2]  # sin horas compartidas no se puede decir nada
    assert "misma ventana de 3 minutos 0 veces" in alt.explain(0, 1)[0]


def test_stylometry_evidence_shows_rare_shared_traits(world, analysis):
    keys = analysis.keys
    pairs = [(i, j) for i in range(len(keys)) for j in range(i + 1, len(keys))
             if world.same(keys[i], keys[j]) and analysis.corpus.n_posts[[i, j]].min() >= 40]
    assert pairs
    by = {s.name: s for s in analysis.signals}
    i, j = max(pairs, key=lambda ij: analysis.scores[ij])
    text, ev = by["stylometry.char_ngrams"].explain(i, j)
    assert "n-gramas" in text and ev
    assert any(f"de {analysis.corpus.n} cuentas" in e.description for e in ev)
    assert any(e.a and e.b for e in ev)
