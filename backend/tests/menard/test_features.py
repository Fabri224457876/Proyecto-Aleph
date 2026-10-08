import math

import numpy as np

from aleph.menard import lexicon as lx
from aleph.menard.features import HABIT_DIMS, HABIT_LABELS, clean_text, extract, normalize_handle

from .conftest import make_profile


def _dim(group, needle):
    return next(i for i, (lab, _) in enumerate(HABIT_LABELS[group]) if needle in lab)


def test_clean_text_neutralizes_urls_mentions_hashtags():
    assert clean_text("mirá esto https://x.example/a @pepe #futbol") == "mirá esto @ #"


def test_strip_accents_keeps_enie():
    assert lx.strip_accents("también ñandú CANCIÓN") == "tambien ñandu CANCION"


def test_laugh_and_elongation_detection():
    found = lx.LAUGH_RE.findall("jajaja no puede ser JAJSJAJ holaaa lol xD")
    assert found == ["jajaja", "JAJSJAJ", "lol", "xD"]
    assert lx.laugh_family("jajaja") == "ja" and lx.laugh_family("jsjsjs") == "jsjs"
    assert lx.laugh_family("haha") == "ha" and lx.laugh_family("ajajaj") == "ajaj"
    assert lx.ELONG_RE.search("holaaa") and not lx.ELONG_RE.search("hola")


def test_habit_vectors_have_declared_dimensions():
    f = extract(make_profile("a", ["Hola, qué tal? todo bien."] * 6))
    for group, dim in HABIT_DIMS.items():
        assert f.habits[group].shape == (dim,), group


def test_orthography_and_abbreviation_ratios():
    texts = ["xq no vino? q raro, tambien me pasa", "xq si, q se yo", "porque tambien queria q vengas",
             "despues vemos q hacemos xq hoy no", "q dia de locos, tambien ayer", "xq q"]
    f = extract(make_profile("a", texts))
    ortho = f.habits["ortho"]
    assert ortho[lx.VARIANT_DIMS.index(("porque", "xq"))] == 4 / 5
    assert ortho[lx.VARIANT_DIMS.index(("que", "q"))] == 1.0
    assert ortho[-1] == 1.0  # todas las tildes omitidas (tambien, despues, dia, queria)
    g = extract(make_profile("b", ["También vino después.", "Qué día, también ayer.",
                                   "Después vemos, también."] * 2))
    assert g.habits["ortho"][-1] == 0.0


def test_undefined_ratios_are_nan_not_zero():
    f = extract(make_profile("a", ["hola", "chau", "bueno", "dale", "listo"]))
    assert math.isnan(f.habits["punct"][_dim("punct", "abiertas con ¿")])  # no hubo preguntas
    assert math.isnan(f.habits["elong"][_dim("elong", "largo medio de la risa")])
    assert np.isnan(f.habits["fw"]).all()  # muy pocas palabras


def test_emoji_rioplatense_and_behavior_sets():
    p = make_profile("a", ["che vos tenés razón 😂😂", "re copado el laburo 😂", "mirá vos che 🔥"] * 15)
    p.posts[0].hashtags = ["Futbol"]
    p.posts[0].urls = ["https://www.Diario.example/nota/1"]
    p.posts[1].mentions = ["Pepe"]
    p.posts[2].reply_to = "pepe"
    p.posts[2].client = "Twitter for Android"
    f = extract(p)
    assert f.sets["emoji"]["😂"] == 45 and f.sets["emoji"]["🔥"] == 15
    assert f.habits["rio"][lx.RIO_DIMS.index("che")] > 0
    assert f.habits["rio"][lx.RIO_DIMS.index("voseo")] > 0
    assert f.sets["hashtags"] == {"futbol": 1} and f.sets["domains"] == {"diario.example": 1}
    assert f.sets["targets"]["pepe"] == 2
    assert f.sets["clients"]["familia:android"] == 1


def test_reposts_are_not_authored_text():
    p = make_profile("a", ["RT @otro: TEXTO AJENO EN MAYÚSCULAS!!!"] * 5 + ["texto propio en minúscula"] * 5)
    for post in p.posts[:5]:
        post.kind = "repost"
    f = extract(p)
    assert f.n_posts == 10 and f.n_authored == 5
    assert all("AJENO" not in t for t in f.texts)


def test_handle_normalization_leet_and_digits():
    assert normalize_handle("N1c0_Dev_87")[1] == "nicodev"
    assert normalize_handle("@Nico.Dev")[1] == "nicodev"
    assert normalize_handle("nico_dev_2001")[1] == "nicodev"
