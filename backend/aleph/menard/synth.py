"""Generador de dataset sintético para MENARD.

Fabrica "personas" ficticias con idiolecto propio (puntuación, muletillas, errores, emojis, horario,
huso, temas) y les crea 1–4 cuentas en distintas plataformas. Determinístico por semilla.
Nada acá corresponde a personas o cuentas reales.

Dificultades incluidas a propósito:
- arquetipos: personas distintas con estilos casi clonados;
- distractores: muchas personas comparten tema, hashtags, dominios, cuentas seguidas y eventos;
- amistades: personas distintas que se mencionan y comparten círculo;
- cuentas disfrazadas: el operador cambia parte de sus hábitos, handle, avatar y horarios;
- cuentas alternativas sobre otro tema y con franja horaria distinta;
- cuentas con pocas publicaciones y variación de hábitos entre plataformas.

Uso:  python -m aleph.menard.synth --seed 7 --personas 60 --out data/synthetic/menard_demo.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from aleph.core.schemas import AccountProfile, AccountRecord, PostRecord

from . import synth_data as sd
from .lexicon import VARIANT_GROUPS, VOWELS, strip_accents

PLATFORMS = ("twitter", "instagram", "discord")
EPOCH0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
SPAN_DAYS = 540
_SLOT_RE = re.compile(r"\{(\w+)(?::(\w+))?\}")
_TOK_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
_ES_ABBR = ["que", "porque", "por", "para", "de", "tambien", "bien", "estoy", "esta", "gracias",
            "despues", "nada", "mucho", "bueno", "tampoco", "igual"]
_EN_ABBR = ["you", "your", "are", "please", "thanks", "because", "though", "people", "don't",
            "i'm", "can't", "that's"]
_PROB_KEYS = {
    "punct": ["end_period", "ellipsis", "excl", "multi_excl", "multi_q", "open_marks",
              "comma_keep", "space_before", "nospace_comma"],
    "caps": ["upper_start", "caps_word", "caps_post"],
    "laugh": ["laugh", "laugh_end", "elong", "elong_vowel"],
    "emoji": ["emoji_end", "emoji_repeat", "emoticon_p", "emoji_p"],
    "ortho": ["acc_omit", "miss_apply"],
    "fillers": ["filler", "filler_start"],
}


@dataclass
class Idiolect:
    dialect: str  # "rio" | "neutral"
    p_en: float
    p: dict[str, float]
    slots: dict[str, np.ndarray]
    en_slots: dict[str, np.ndarray]
    tmpl: np.ndarray
    en_tmpl: np.ndarray
    abbr: dict[str, tuple[str, float]]
    miss: list[int]
    en_miss: list[int]
    laughs: list[str]
    palette: list[str]
    palette_w: np.ndarray
    emoticon: str
    fillers: list[str]
    en_fillers: list[str]
    joiner_w: np.ndarray
    sent_lambda: float
    emoji_rate: float


@dataclass
class SynthWorld:
    profiles: list[AccountProfile]
    persona_of: dict[str, int]
    meta: dict[str, dict] = field(default_factory=dict)
    seed: int = 0

    def same(self, a: str, b: str) -> bool:
        return self.persona_of[a] == self.persona_of[b]

    def labels(self, keys: list[str]) -> np.ndarray:
        p = np.array([self.persona_of[k] for k in keys])
        return p[:, None] == p[None, :]


# ------------------------------------------------------------------------------------------------
# utilidades de muestreo
# ------------------------------------------------------------------------------------------------
def _pick(rng: np.random.Generator, w: np.ndarray) -> int:
    c = np.cumsum(w)
    return int(min(np.searchsorted(c, rng.random() * c[-1]), len(w) - 1))


def _logit_jitter(rng: np.random.Generator, p: float, sigma: float) -> float:
    if p <= 0.005:
        return 0.12 if rng.random() < 0.06 else 0.0
    if p >= 0.995:
        return 0.9 if rng.random() < 0.06 else 1.0
    x = np.log(p / (1 - p)) + rng.normal(0, sigma)
    return float(1 / (1 + np.exp(-x)))


def _dirichlet(rng: np.random.Generator, n: int, alpha: float) -> np.ndarray:
    return rng.dirichlet(np.full(n, alpha)) + 1e-4


def _zipf_pref(rng: np.random.Generator, n: int, noise: float = 1.0) -> np.ndarray:
    return (1.0 / np.arange(1, n + 1) ** 0.8) * rng.lognormal(0, noise, n)


def sample_idiolect(rng: np.random.Generator, lang: str | None = None) -> Idiolect:
    if lang is None:
        lang = str(rng.choice(["rio", "neutral", "en", "bilingual"], p=[0.72, 0.10, 0.12, 0.06]))
    dialect = "neutral" if lang == "neutral" else "rio"
    p_en = {"en": 0.97, "bilingual": float(rng.uniform(0.15, 0.4))}.get(lang, float(rng.beta(0.5, 12)))
    texter = float(rng.beta(0.7, 1.6))  # tendencia general a abreviar y descuidar la ortografía
    formal = rng.random() < 0.45
    p = {
        "end_period": float(rng.beta(0.5, 1.5)) * (1.3 if formal else 0.6),
        "ellipsis": float(rng.beta(0.4, 3)),
        "excl": float(rng.beta(1, 4)),
        "multi_excl": float(rng.beta(0.4, 3)),
        "multi_q": float(rng.beta(0.4, 3)),
        "open_marks": float(rng.beta(4, 1.5)) if rng.random() < 0.3 else float(rng.beta(0.5, 8)),
        "comma_keep": float(rng.beta(3, 1.5)),
        "space_before": float(rng.beta(2, 2)) if rng.random() < 0.1 else 0.0,
        "nospace_comma": float(rng.beta(2, 2)) if rng.random() < 0.15 else 0.0,
        "upper_start": float(rng.beta(6, 1.2)) if formal else float(rng.beta(0.8, 5)),
        "caps_word": float(rng.beta(0.3, 6)),
        "caps_post": float(rng.beta(0.2, 12)),
        "laugh": float(rng.beta(0.8, 4)),
        "laugh_end": float(rng.beta(4, 1.5)),
        "elong": float(rng.beta(0.5, 4)),
        "elong_vowel": float(rng.beta(4, 1)),
        "emoji_end": float(rng.beta(4, 1.5)),
        "emoji_repeat": float(rng.beta(0.5, 3)),
        "emoji_p": 0.0 if rng.random() < 0.22 else float(rng.beta(1.2, 2.5)),
        "emoticon_p": float(rng.beta(1, 5)) if rng.random() < 0.25 else 0.0,
        "acc_omit": float(np.clip(rng.beta(0.6, 0.6) * (0.5 + texter), 0, 1)),
        "miss_apply": float(rng.beta(4, 2)),
        "filler": float(rng.beta(1, 4)),
        "filler_start": float(rng.beta(2, 2)),
    }
    p = {k: float(np.clip(v, 0.0, 1.0)) for k, v in p.items()}

    def slot_prefs(slots: dict[str, list[str]], es: bool) -> dict[str, np.ndarray]:
        out = {}
        for k, opts in slots.items():
            w = _dirichlet(rng, len(opts), 0.45)
            if es:
                bad = sd.NEUTRAL_ONLY if dialect == "rio" else sd.RIO_ONLY
                w = w * np.array([0.03 if o in bad else 1.0 for o in opts])
            out[k] = w / w.sum()
        return out

    abbr: dict[str, tuple[str, float]] = {}
    for canon in _ES_ABBR + _EN_ABBR:
        if rng.random() < 0.55 * texter:
            variants = [v for v in VARIANT_GROUPS[canon] if v != "igualmente"]
            vw = 1.0 / np.arange(1, len(variants) + 1) ** 1.5
            abbr[canon] = (variants[_pick(rng, vw)], float(rng.beta(2.5, 1.5)))
    pm = 0.03 + 0.12 * texter
    n_pal = int(rng.integers(3, 9))
    pal_idx = rng.choice(len(sd.EMOJIS), size=n_pal, replace=False,
                         p=_norm(1.0 / np.arange(1, len(sd.EMOJIS) + 1) ** 0.9))
    n_l = 1 + int(rng.random() < 0.35)
    l_idx = rng.choice(len(sd.LAUGHS), size=n_l, replace=False,
                       p=_norm(1.0 / np.arange(1, len(sd.LAUGHS) + 1) ** 1.0))
    return Idiolect(
        dialect=dialect, p_en=p_en, p=p,
        slots=slot_prefs(sd.ES_SLOTS, True), en_slots=slot_prefs(sd.EN_SLOTS, False),
        tmpl=_dirichlet(rng, len(sd.ES_TEMPLATES), 0.8),
        en_tmpl=_dirichlet(rng, len(sd.EN_TEMPLATES), 0.8),
        abbr=abbr,
        miss=[i for i in range(len(sd.MISSPELL_RULES_ES)) if rng.random() < pm],
        en_miss=[i for i in range(len(sd.MISSPELL_RULES_EN)) if rng.random() < pm],
        laughs=[sd.LAUGHS[i] for i in l_idx],
        palette=[sd.EMOJIS[i] for i in pal_idx], palette_w=_zipf_pref(rng, n_pal, 0.5),
        emoticon=sd.EMOTICONS[_pick(rng, 1.0 / np.arange(1, len(sd.EMOTICONS) + 1))],
        fillers=[sd.FILLERS_ES[i] for i in rng.choice(len(sd.FILLERS_ES), int(rng.integers(1, 5)), replace=False)],
        en_fillers=[sd.FILLERS_EN[i] for i in rng.choice(len(sd.FILLERS_EN), int(rng.integers(1, 4)), replace=False)],
        joiner_w=_dirichlet(rng, 5, 0.6),
        sent_lambda=float(rng.gamma(2.0, 0.35)),
        emoji_rate=float(rng.gamma(2.0, 0.6)),
    )


def _norm(w: np.ndarray) -> np.ndarray:
    return w / w.sum()


def _clone_idiolect(rng: np.random.Generator, base: Idiolect, sigma: float, mix: float) -> Idiolect:
    """Copia con ruido: sirve para variación entre cuentas (poco ruido) y arquetipos (más ruido)."""
    def mixw(w: np.ndarray) -> np.ndarray:
        return _norm((1 - mix) * _norm(w) + mix * _dirichlet(rng, len(w), 0.6))
    return replace(
        base,
        p={k: _logit_jitter(rng, v, sigma) for k, v in base.p.items()},
        slots={k: mixw(w) for k, w in base.slots.items()},
        en_slots={k: mixw(w) for k, w in base.en_slots.items()},
        tmpl=mixw(base.tmpl), en_tmpl=mixw(base.en_tmpl),
        abbr={k: (v[0], _logit_jitter(rng, v[1], sigma)) for k, v in base.abbr.items()
              if rng.random() > 0.1 * mix * 4},
        palette_w=base.palette_w * rng.lognormal(0, 0.4, len(base.palette_w)),
        joiner_w=mixw(base.joiner_w),
        sent_lambda=base.sent_lambda * float(rng.lognormal(0, 0.2)),
        emoji_rate=base.emoji_rate * float(rng.lognormal(0, 0.3)),
    )


def _platform_adjust(idi: Idiolect, platform: str) -> Idiolect:
    p = dict(idi.p)
    lam, er = idi.sent_lambda, idi.emoji_rate
    if platform == "instagram":
        lam *= 1.4
        er *= 1.5
        p["emoji_p"] = min(1.0, p["emoji_p"] * 1.5)
    elif platform == "discord":
        lam *= 0.55
        p["end_period"] *= 0.5
        p["upper_start"] *= 0.75
        p["emoji_p"] *= 0.7
    return replace(idi, p=p, sent_lambda=lam, emoji_rate=er)


def _disguise(rng: np.random.Generator, idi: Idiolect, donor: Idiolect, strength: float) -> Idiolect:
    """El operador intenta escribir distinto: reemplaza grupos de hábitos por los de otra persona.
    La ortografía (tildes, errores) es lo más difícil de controlar y se cambia menos."""
    p = dict(idi.p)
    out = replace(idi, p=p)
    for group, keys in _PROB_KEYS.items():
        if rng.random() < (strength if group != "ortho" else 0.35 * strength):
            for k in keys:
                p[k] = donor.p[k]
            if group == "punct":
                out.joiner_w = donor.joiner_w
            elif group == "laugh":
                out.laughs = donor.laughs
            elif group == "emoji":
                out.palette, out.palette_w, out.emoticon = donor.palette, donor.palette_w, donor.emoticon
                out.emoji_rate = donor.emoji_rate
            elif group == "ortho":
                out.miss, out.en_miss = donor.miss, donor.en_miss
            elif group == "fillers":
                out.fillers, out.en_fillers = donor.fillers, donor.en_fillers
    if rng.random() < strength:
        out.abbr = donor.abbr
    if rng.random() < strength:
        out.slots = {k: _norm(0.3 * idi.slots[k] + 0.7 * donor.slots[k]) for k in idi.slots}
        out.en_slots = {k: _norm(0.3 * idi.en_slots[k] + 0.7 * donor.en_slots[k]) for k in idi.en_slots}
    if rng.random() < strength:
        out.tmpl, out.en_tmpl = donor.tmpl, donor.en_tmpl
    return out


# ------------------------------------------------------------------------------------------------
# generación de texto
# ------------------------------------------------------------------------------------------------
def compose(rng: np.random.Generator, idi: Idiolect, topic: str, item_w: dict, en: bool) -> str:
    p = idi.p
    templates = sd.EN_TEMPLATES if en else sd.ES_TEMPLATES
    slots = sd.EN_SLOTS if en else sd.ES_SLOTS
    prefs = idi.en_slots if en else idi.slots
    tw = idi.en_tmpl if en else idi.tmpl
    t = sd.TOPICS[topic]
    s_key, o_key = ("S_en", "O_en") if en else ("S", "O")

    def fill(m: re.Match) -> str:
        name, arg = m.group(1), m.group(2)
        if name == "S":
            return t[s_key][_pick(rng, item_w[(topic, s_key)])]
        if name == "O":
            return t[o_key][_pick(rng, item_w[(topic, o_key)])]
        if name == "V":
            vos = (idi.dialect == "rio") != (rng.random() < 0.03)
            return sd.V2[arg][0 if vos else 1]
        return slots[name][_pick(rng, prefs[name])]

    n_sent = 1 + min(int(rng.poisson(idi.sent_lambda)), 3)
    joiners = [". ", ", ", " ", "... ", "\n"]
    parts: list[str] = []
    for k in range(n_sent):
        s = _SLOT_RE.sub(fill, templates[_pick(rng, tw)])
        s = re.sub(r"\s+", " ", s).strip(" ,")
        s = re.sub(r"^, ?| ,", lambda m: "," if m.group(0) == " ," else "", s)
        question = s.endswith("?")
        body = s[:-1] if question else s
        # comas
        body = "".join(ch for ch in body if ch != "," or rng.random() < p["comma_keep"])
        if p["nospace_comma"] and rng.random() < p["nospace_comma"]:
            body = body.replace(", ", ",")
        last = k == n_sent - 1
        if question:
            end = "??" if rng.random() < p["multi_q"] else "?"
            if not en and rng.random() < p["open_marks"]:
                body = "¿" + body
        else:
            r = rng.random()
            if r < p["excl"]:
                end = ("!!" + "!" * int(rng.integers(0, 3))) if rng.random() < p["multi_excl"] else "!"
                if not en and rng.random() < p["open_marks"]:
                    body = "¡" + body
            elif r < p["excl"] + p["ellipsis"] * (1 - p["excl"]):
                end = "..."
            elif last:
                end = "." if rng.random() < p["end_period"] else ""
            else:
                end = ""
        if end and p["space_before"] and rng.random() < p["space_before"]:
            end = " " + end
        parts.append(body + end)
    text = parts[0]
    for nxt in parts[1:]:
        if text[-1] in ".!?":
            j = "\n" if rng.random() < idi.joiner_w[4] / idi.joiner_w.sum() else " "
            if rng.random() < p["upper_start"]:
                nxt = _cap_first(nxt)
        else:
            j = joiners[_pick(rng, idi.joiner_w)]
            if j in (". ", "... ", "\n") and rng.random() < p["upper_start"]:
                nxt = _cap_first(nxt)
        text += j + nxt

    # errores recurrentes, abreviaturas y tildes
    rules = sd.MISSPELL_RULES_EN if en else sd.MISSPELL_RULES_ES
    for i in (idi.en_miss if en else idi.miss):
        if rng.random() < p["miss_apply"]:
            text = re.sub(rules[i][0], rules[i][1], text)

    def tok(m: re.Match) -> str:
        w = m.group(0)
        low = w.lower()
        na = strip_accents(low)
        ab = idi.abbr.get(na if na != "esta" or low == "está" else "")
        if ab and rng.random() < ab[1] and (w == low):
            return ab[0]
        if low != na and rng.random() < p["acc_omit"]:
            return strip_accents(w)
        return w

    text = _TOK_RE.sub(tok, text)

    words = [m for m in _TOK_RE.finditer(text) if len(m.group(0)) >= 3]
    if words and rng.random() < p["elong"]:
        m = words[int(rng.integers(len(words)))]
        w = m.group(0)
        k = int(rng.integers(2, 6))
        vpos = [i for i, ch in enumerate(w) if ch.lower() in VOWELS]
        pos = vpos[-1] if (vpos and rng.random() < p["elong_vowel"]) else len(w) - 1
        text = text[: m.start()] + w[: pos + 1] + w[pos] * k + w[pos + 1:] + text[m.end():]
    if words and rng.random() < p["caps_word"]:
        m = words[int(rng.integers(len(words)))]
        if m.end() <= len(text) and text[m.start():m.end()] == m.group(0):
            text = text[: m.start()] + m.group(0).upper() + text[m.end():]

    fillers = idi.en_fillers if en else idi.fillers
    if fillers and rng.random() < p["filler"]:
        f = fillers[int(rng.integers(len(fillers)))]
        if rng.random() < p["filler_start"]:
            text = f + ("," if rng.random() < p["comma_keep"] else "") + " " + text
        else:
            text = text + " " + f
    if rng.random() < p["laugh"]:
        base = idi.laughs[int(rng.integers(len(idi.laughs)))]
        unit = base[-2:] if len(base) > 3 and rng.random() < 0.5 else ""
        laugh = base + unit * int(rng.integers(0, 3))
        text = (text + " " + laugh) if rng.random() < p["laugh_end"] else (laugh + " " + text)
    if rng.random() < p["upper_start"]:
        text = _cap_first(text)
    if rng.random() < p["caps_post"]:
        text = text.upper()
    if rng.random() < p["emoticon_p"]:
        text += " " + idi.emoticon
    if rng.random() < p["emoji_p"]:
        n_e = 1 + min(int(rng.poisson(idi.emoji_rate * 0.5)), 3)
        em = ""
        for _ in range(n_e):
            e = idi.palette[_pick(rng, idi.palette_w)]
            em += e * (int(rng.integers(2, 4)) if rng.random() < p["emoji_repeat"] else 1)
        if rng.random() < p["emoji_end"] or "\n" not in text and " " not in text:
            text = text + " " + em
        else:
            cut = text.find(" ", len(text) // 2)
            text = text + " " + em if cut < 0 else text[:cut] + " " + em + text[cut:]
    return text


def _cap_first(s: str) -> str:
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ch.upper() + s[i + 1:]
    return s


# ------------------------------------------------------------------------------------------------
# personas y mundo
# ------------------------------------------------------------------------------------------------
@dataclass
class _Persona:
    pid: int
    idi: Idiolect
    topics: list[str]
    item_w: dict
    tz: int
    hour_w: np.ndarray  # 24, hora local
    week_w: np.ndarray
    device: str
    kind_mix: np.ndarray
    circle: dict[str, list[str]]
    circle_w: np.ndarray
    tag_noise: dict[str, np.ndarray]
    personal_tags: list[str]
    personal_domains: list[str]
    tag_rate: float
    url_rate: float
    mention_rate: float
    base: str
    display: str
    bio_parts: list[str]
    bio_sep: str
    avatar: int
    p_sync: float
    self_mention: bool
    batch_created: bool
    reuse_avatar: bool
    archetype: int
    friends: list[int] = field(default_factory=list)
    accounts: list[dict] = field(default_factory=list)


def _radical(rng: np.random.Generator) -> str:
    r = sd.NICKS[int(rng.integers(len(sd.NICKS)))]
    if rng.random() < 0.6:
        r += "".join(sd.SYLL[int(rng.integers(len(sd.SYLL)))] for _ in range(int(rng.integers(1, 3))))
    return r


def _handle_variant(rng: np.random.Generator, base: str) -> str:
    k = int(rng.integers(0, 9))
    num = str(int(rng.integers(1, 100))) if rng.random() < 0.6 else str(int(rng.integers(1985, 2008)))
    if k == 0:
        return base + num
    if k == 1:
        return base + "_" + num
    if k == 2:
        return base + "." + sd.HANDLE_WORDS[int(rng.integers(len(sd.HANDLE_WORDS)))]
    if k == 3:
        return "el" + base
    if k == 4:
        return base.translate(str.maketrans("aeios", "43105")) + (num if rng.random() < 0.4 else "")
    if k == 5:
        return "x" + base + "x"
    if k == 6:
        return base + "_" + sd.HANDLE_WORDS[int(rng.integers(len(sd.HANDLE_WORDS)))]
    if k == 7:
        return base.capitalize() + num
    return base


def _hour_weights(rng: np.random.Generator) -> np.ndarray:
    sleep_start = rng.normal(0.8, 1.3) % 24
    sleep_len = rng.uniform(6.5, 8.5)
    hrs = np.arange(24) + 0.5
    w = np.full(24, 0.3)
    for _ in range(int(rng.integers(1, 4))):
        c = (sleep_start + sleep_len + rng.uniform(0.5, 24 - sleep_len - 0.5)) % 24
        d = np.abs(hrs - c)
        d = np.minimum(d, 24 - d)
        w += rng.uniform(0.5, 2.0) * np.exp(-0.5 * (d / rng.uniform(1.0, 3.0)) ** 2)
    asleep = ((hrs - sleep_start) % 24) < sleep_len
    w[asleep] = 0.004
    return w / w.sum()


def _make_persona(rng: np.random.Generator, pid: int, parent: "_Persona | None") -> _Persona:
    topics_all = list(sd.TOPICS)
    if parent is not None:
        idi = _clone_idiolect(rng, parent.idi, sigma=0.35, mix=0.2)
        topics = list(parent.topics)
        tz = parent.tz
        archetype = parent.archetype
    else:
        idi = sample_idiolect(rng)
        topics = [topics_all[i] for i in rng.choice(len(topics_all), int(rng.integers(1, 4)), replace=False)]
        tz = int(rng.choice([-3, -3, -3, -3, -3, -3, -3, -4, -5, -6, 1, 2, -8, 0]))
        if idi.p_en > 0.9:
            tz = int(rng.choice([-5, -8, 0, 1, -6, 10]))
        archetype = pid
    item_w = {}
    for tname, t in sd.TOPICS.items():
        for k in ("S", "O", "S_en", "O_en"):
            item_w[(tname, k)] = _zipf_pref(rng, len(t[k]), 1.0)
    circle_n = int(rng.integers(25, 60))
    names = [f"{sd.NICKS[int(rng.integers(len(sd.NICKS)))]}{sd.SYLL[int(rng.integers(len(sd.SYLL)))]}"
             f"{sd.SYLL[int(rng.integers(len(sd.SYLL)))]}{int(rng.integers(10, 9999))}" for _ in range(circle_n)]
    circle = {pl: [n if rng.random() < 0.5 else f"{n}_{pl[:2]}" for n in names] for pl in PLATFORMS}
    first = sd.FIRST_NAMES[int(rng.integers(len(sd.FIRST_NAMES)))]
    display = first + (" " + sd.SURNAMES[int(rng.integers(len(sd.SURNAMES)))] if rng.random() < 0.6 else "")
    bio_pool = [sd.PROFESSIONS[int(rng.integers(len(sd.PROFESSIONS)))],
                sd.CITIES[int(rng.integers(len(sd.CITIES)))],
                sd.QUOTES[int(rng.integers(len(sd.QUOTES)))],
                "fan de " + sd.TOPICS[topics[0]]["S"][int(rng.integers(6))],
                sd.QUOTES[int(rng.integers(len(sd.QUOTES)))]]
    return _Persona(
        pid=pid, idi=idi, topics=topics, item_w=item_w, tz=tz, hour_w=_hour_weights(rng),
        week_w=_norm(rng.lognormal(0, 0.35, 7)),
        device=str(rng.choice(["android", "iphone", "web"], p=[0.5, 0.3, 0.2])),
        kind_mix=rng.dirichlet([5, 3, 2, 1]), circle=circle,
        circle_w=_zipf_pref(rng, circle_n, 0.6),
        tag_noise={t: rng.lognormal(0, 1.0, len(sd.TOPICS[t]["tags"])) for t in sd.TOPICS},
        personal_tags=[f"{sd.SYLL[int(rng.integers(len(sd.SYLL)))]}{sd.HANDLE_WORDS[int(rng.integers(len(sd.HANDLE_WORDS)))]}"
                       f"{int(rng.integers(100))}" for _ in range(int(rng.integers(0, 3)))],
        personal_domains=[f"{_radical(rng)}{int(rng.integers(100))}.example" for _ in range(int(rng.integers(0, 3)))],
        tag_rate=float(rng.beta(1.2, 3)), url_rate=float(rng.beta(1, 6)),
        mention_rate=float(rng.beta(1.2, 5)),
        base=_radical(rng), display=display,
        bio_parts=[bio_pool[i] for i in rng.choice(5, int(rng.integers(2, 5)), replace=False)],
        bio_sep=sd.BIO_SEPS[int(rng.integers(len(sd.BIO_SEPS)))],
        avatar=int(rng.integers(0, 2**63)), p_sync=float(rng.beta(1.2, 4)),
        self_mention=bool(rng.random() < 0.25), batch_created=bool(rng.random() < 0.3),
        reuse_avatar=bool(rng.random() < 0.35),
        archetype=archetype,
    )


def _n_posts(rng: np.random.Generator) -> int:
    r = rng.random()
    if r < 0.03:
        return int(rng.integers(1, 5))
    if r < 0.22:
        return int(rng.integers(5, 13))
    if r < 0.47:
        return int(rng.integers(13, 31))
    if r < 0.82:
        return int(rng.integers(31, 81))
    return int(rng.integers(81, 181))


def generate_world(seed: int = 0, n_personas: int = 120, difficulty: float = 1.0) -> SynthWorld:
    """Genera un mundo sintético. `difficulty` escala disfraz, arquetipos y variación entre cuentas."""
    rng = np.random.default_rng(seed)
    d = float(difficulty)
    personas: list[_Persona] = []
    for pid in range(n_personas):
        parent = personas[int(rng.integers(len(personas)))] if personas and rng.random() < 0.3 * d else None
        personas.append(_make_persona(rng, pid, parent))
    voices = [sample_idiolect(rng) for _ in range(12)]

    # amistades: comparten parte del círculo y se mencionan
    for p in personas:
        if rng.random() < 0.4:
            cands = [q for q in personas if q.pid != p.pid and set(q.topics) & set(p.topics)]
            if cands:
                q = cands[int(rng.integers(len(cands)))]
                p.friends.append(q.pid)
                q.friends.append(p.pid)
                k = int(0.4 * min(len(p.circle["twitter"]), len(q.circle["twitter"])))
                for pl in PLATFORMS:
                    p.circle[pl][:k] = q.circle[pl][:k]

    popular = {pl: {t: [f"{t}{w}{i}" + ("" if pl == "twitter" else f"_{pl[:2]}" if i % 3 else "")
                        for i, w in enumerate(["info", "ya", "total", "hoy", "club", "tv", "fans",
                                               "news", "ok", "plus"] * 4)]
                    for t in sd.TOPICS} for pl in PLATFORMS}
    celeb = {pl: [f"famoso{i}" + ("" if i % 2 else f"_{pl[:2]}") for i in range(60)] for pl in PLATFORMS}
    events = {t: [(day, int(rng.choice([21, 22, 23, 0, 1, 2]))) for day in range(int(rng.integers(0, 7)), SPAN_DAYS, 7)]
              for t in sd.TOPICS}

    # --- cuentas (sin publicaciones todavía) ---------------------------------------------------
    used_handles: set[tuple[str, str]] = set()
    for p in personas:
        n_acc = int(rng.choice([1, 2, 3, 4], p=[0.55, 0.25, 0.13, 0.07]))
        start0 = float(rng.uniform(0, 300))
        donor = sample_idiolect(rng)
        for a in range(n_acc):
            platform = str(rng.choice(PLATFORMS, p=[0.5, 0.25, 0.25]))
            disguised = bool(a > 0 and rng.random() < 0.22 * d)
            idi = _clone_idiolect(rng, p.idi, sigma=0.45 * d, mix=0.12 * d)
            if disguised:
                idi = _disguise(rng, idi, donor, strength=float(rng.uniform(0.5, 0.9)))
            idi = _platform_adjust(idi, platform)
            topics = list(p.topics)
            if a > 0 and rng.random() < 0.5:
                if rng.random() < 0.5 * d:
                    others = [t for t in sd.TOPICS if t not in p.topics]
                    topics = [others[int(rng.integers(len(others)))]]
                else:
                    topics = [p.topics[int(rng.integers(len(p.topics)))]]
            related = a == 0 or (not disguised and rng.random() < 0.45)
            base = p.base if related else _radical(rng)
            for _ in range(50):
                handle = _handle_variant(rng, base)
                if (platform, handle.lower()) not in used_handles:
                    break
                base = base + sd.SYLL[int(rng.integers(len(sd.SYLL)))]
            used_handles.add((platform, handle.lower()))
            start = start0 + rng.normal(0, 30) if rng.random() < 0.75 else rng.uniform(0, 300)
            start = float(np.clip(start, 0, SPAN_DAYS - 100))
            end = float(min(SPAN_DAYS - 1, start + rng.uniform(90, 400)))
            window = None
            if a > 0 and rng.random() < 0.3 * d or disguised and rng.random() < 0.6:
                lo = float(rng.uniform(8, 20))
                window = (lo, lo + float(rng.uniform(5, 9)))
            hw = p.hour_w * rng.lognormal(0, 0.25, 24)
            if window:
                h = np.arange(24) + 0.5
                inside = ((h - window[0]) % 24) < (window[1] - window[0])
                hw = hw * np.where(inside, 1.0, 0.08)
            if disguised or rng.random() < 0.5:
                bio_parts = [sd.PROFESSIONS[int(rng.integers(len(sd.PROFESSIONS)))],
                             sd.QUOTES[int(rng.integers(len(sd.QUOTES)))],
                             sd.CITIES[int(rng.integers(len(sd.CITIES)))]][: int(rng.integers(0, 4))]
                sep = sd.BIO_SEPS[int(rng.integers(len(sd.BIO_SEPS)))]
            else:
                order = rng.permutation(len(p.bio_parts))[: max(2, int(rng.integers(2, len(p.bio_parts) + 1)))]
                bio_parts, sep = [p.bio_parts[i] for i in order], p.bio_sep
            r = rng.random()
            if r < 0.08:
                avatar = 0x0F0F0F0F0F0F0F0F if platform != "discord" else 0x00FF00FF00FF00FF
            elif p.reuse_avatar and r < 0.8 and not disguised:
                avatar = p.avatar
                for bit in rng.integers(0, 63, int(rng.integers(0, 6))):
                    avatar ^= 1 << int(bit)
            else:
                avatar = int(rng.integers(0, 2**63))
            if p.batch_created and not disguised:
                created = start - 20 + rng.normal(0, 6)
            else:
                created = rng.uniform(-3000, start - 1)
            if disguised or rng.random() < 0.45:
                display = sd.FIRST_NAMES[int(rng.integers(len(sd.FIRST_NAMES)))] if rng.random() < 0.7 else handle
            else:
                display = p.display if rng.random() < 0.7 else p.display.split()[0]
            device = p.device if rng.random() < 0.75 else str(rng.choice(["android", "iphone", "web"]))
            p.accounts.append(dict(
                platform=platform, handle=handle, idi=idi, topics=topics, start=start, end=end,
                hour_w=_norm(hw), disguised=disguised, n=_n_posts(rng), bio=sep.join(bio_parts),
                avatar=avatar, created=created, display=display, device=device,
                third=(sd.THIRD_PARTY_CLIENTS[int(rng.integers(len(sd.THIRD_PARTY_CLIENTS)))]
                       if rng.random() < 0.06 else ""),
                has_lists=bool(rng.random() < 0.6), times=[], window=window is not None,
                # cuánto del círculo personal arrastra esta cuenta (las alternativas, poco o nada)
                circle_use=(1.0 if a == 0 else float(rng.uniform(0, 0.1)) if disguised else
                            float(rng.uniform(0.05, 0.3)) if rng.random() < 0.5 else float(rng.uniform(0.5, 1.0))),
                own_domains=bool(not disguised and (a == 0 or rng.random() < 0.6)),
            ))

    # --- tiempos -------------------------------------------------------------------------------
    for p in personas:
        for acc in p.accounts:
            if acc["n"] >= 20:
                for t in acc["topics"]:
                    for day, hour in events[t]:
                        if acc["start"] <= day <= acc["end"] and rng.random() < 0.25:
                            t0 = day * 86400 + hour * 3600 + rng.uniform(0, 3000)
                            acc["times"].extend(t0 + 90 * i + rng.uniform(0, 60)
                                                for i in range(int(rng.integers(1, 4))))
        for acc in p.accounts:
            days = np.arange(int(acc["start"]), int(acc["end"]) + 1)
            dw = np.cumsum(p.week_w[(days + 0) % 7])
            cum_h = np.cumsum(acc["hour_w"])
            guard = 0
            while len(acc["times"]) < acc["n"] and guard < 2000:
                guard += 1
                day = int(days[min(np.searchsorted(dw, rng.random() * dw[-1]), len(days) - 1)])
                hour = int(min(np.searchsorted(cum_h, rng.random()), 23))
                t = day * 86400 + ((hour - p.tz) % 24) * 3600 + rng.uniform(0, 3600)
                burst = min(int(rng.geometric(0.55)), 6)
                for _ in range(burst):
                    acc["times"].append(t)
                    t += rng.uniform(40, 360)
                if len(p.accounts) > 1 and rng.random() < p.p_sync:
                    others = [o for o in p.accounts if o is not acc and o["start"] <= day <= o["end"]
                              and len(o["times"]) < o["n"]]
                    if others:
                        o = others[int(rng.integers(len(others)))]
                        t += rng.uniform(240, 1500)
                        for _ in range(min(int(rng.geometric(0.55)), 4)):
                            o["times"].append(t)
                            t += rng.uniform(40, 360)
        merged = []
        for ai, acc in enumerate(p.accounts):
            ts = np.array(acc["times"])
            if len(ts) > acc["n"]:
                ts = ts[rng.choice(len(ts), acc["n"], replace=False)]
            merged.extend((float(t), ai) for t in ts)
            acc["times"] = []
        merged.sort()
        last_t, last_a = -1e18, -1
        for t, ai in merged:  # una persona no publica desde dos cuentas a la vez
            gap = float(rng.uniform(240, 540)) if ai != last_a else 15.0
            t = max(t, last_t + gap)
            p.accounts[ai]["times"].append(t)
            last_t, last_a = t, ai

    # --- publicaciones ---------------------------------------------------------------------------
    by_pid = {p.pid: p for p in personas}
    profiles: list[AccountProfile] = []
    persona_of: dict[str, int] = {}
    meta: dict[str, dict] = {}
    audience = {t: [f"lector{t[:3]}{i}" for i in range(300)] for t in sd.TOPICS}
    for p in personas:
        for ai, acc in enumerate(p.accounts):
            pl = acc["platform"]
            idi: Idiolect = acc["idi"]
            friend_handles = [o["handle"].lower() for f in p.friends for o in by_pid[f].accounts
                              if o["platform"] == pl]
            own = [o["handle"].lower() for o in p.accounts if o is not acc and o["platform"] == pl]
            topic_w = rng.dirichlet(np.ones(len(acc["topics"])) * 2)
            # cada cuenta tiene su propio uso de hashtags, enlaces y menciones alrededor del de la persona
            tag_rate = _logit_jitter(rng, p.tag_rate, 0.8)
            url_rate = _logit_jitter(rng, p.url_rate, 0.8)
            mention_rate = _logit_jitter(rng, p.mention_rate, 0.8)
            if pl == "twitter":
                kinds_w = p.kind_mix * rng.lognormal(0, 0.7, 4)
            elif pl == "instagram":
                kinds_w = np.array([0.8, 0.2, 0.0, 0.0])
            else:
                kinds_w = np.array([0.55, 0.45, 0.0, 0.0])
            posts: list[PostRecord] = []

            def target(topic: str) -> str:
                r = rng.random()
                if own and p.self_mention and r < 0.08:
                    return own[int(rng.integers(len(own)))]
                if friend_handles and r < 0.2:
                    return friend_handles[int(rng.integers(len(friend_handles)))]
                if r < 0.2 + 0.4 * acc["circle_use"]:
                    return p.circle[pl][_pick(rng, p.circle_w)].lower()
                pop = popular[pl][topic]
                return pop[_pick(rng, 1.0 / np.arange(1, len(pop) + 1))].lower()

            for k, t in enumerate(sorted(acc["times"])):
                topic = acc["topics"][_pick(rng, topic_w)]
                kind = ("original", "reply", "repost", "quote")[_pick(rng, kinds_w)]
                en = rng.random() < idi.p_en
                mentions: list[str] = []
                reply_to = ""
                if kind == "repost":
                    src = target(topic)
                    voice = voices[int(rng.integers(len(voices)))]
                    text = f"RT @{src}: " + compose(rng, voice, topic, p.item_w, voice.p_en > 0.5)
                    mentions.append(src)
                else:
                    text = compose(rng, idi, topic, p.item_w, en)
                    if kind == "reply":
                        reply_to = target(topic)
                        if pl != "instagram" and rng.random() < 0.7:
                            text = f"@{reply_to} " + text
                    if rng.random() < mention_rate:
                        m = target(topic)
                        mentions.append(m)
                        text += f" @{m}"
                tags: list[str] = []
                tag_p = {"twitter": tag_rate, "instagram": min(1.0, 2.5 * tag_rate + 0.2), "discord": 0.0}[pl]
                if kind != "repost" and rng.random() < tag_p:
                    pool = sd.TOPICS[topic]["tags"]
                    tw = p.tag_noise[topic] / np.arange(1, len(pool) + 1)
                    for _ in range(1 if pl == "twitter" else int(rng.integers(1, 5))):
                        tg = (p.personal_tags[int(rng.integers(len(p.personal_tags)))]
                              if p.personal_tags and rng.random() < 0.2 else pool[_pick(rng, tw)])
                        if tg not in tags:
                            tags.append(tg)
                    text += " " + " ".join("#" + tg for tg in tags)
                urls: list[str] = []
                if rng.random() < url_rate * {"twitter": 1.0, "instagram": 0.3, "discord": 0.8}[pl]:
                    r = rng.random()
                    if p.personal_domains and acc["own_domains"] and r < 0.35:
                        dom = p.personal_domains[int(rng.integers(len(p.personal_domains)))]
                    elif r < 0.85:
                        doms = sd.TOPICS[topic]["domains"]
                        dom = doms[_pick(rng, 1.0 / np.arange(1, len(doms) + 1))]
                    else:
                        dom = sd.GLOBAL_DOMAINS[int(rng.integers(len(sd.GLOBAL_DOMAINS)))]
                    urls.append(f"https://{dom}/p/{int(rng.integers(1e6))}")
                    text += " " + urls[0]
                client = ""
                if pl == "twitter":
                    if acc["third"] and rng.random() < 0.5:
                        client = acc["third"]
                    else:
                        client = sd.TWITTER_CLIENTS[acc["device"] if rng.random() < 0.82 else "web"]
                posts.append(PostRecord(
                    platform_post_id=f"{pl[:2]}{p.pid}_{ai}_{k}", text=text,
                    created_at=EPOCH0 + timedelta(seconds=int(t)), lang="en" if en else "es",
                    kind=kind, reply_to=reply_to, mentions=mentions, hashtags=tags, urls=urls,
                    client=client))
            following: list[str] = []
            followers: list[str] = []
            if acc["has_lists"]:
                u = rng.uniform(0.45, 0.9) * acc["circle_use"]
                following = [h.lower() for h in p.circle[pl] if rng.random() < u]
                for t in acc["topics"]:
                    following += [h for i, h in enumerate(popular[pl][t])
                                  if rng.random() < min(0.9, 1.2 / (i + 1) ** 0.6)]
                    followers += [h for h in audience[t] if rng.random() < 0.05]
                following += [h for i, h in enumerate(celeb[pl]) if rng.random() < 0.8 / (i + 1) ** 0.6]
                u2 = rng.uniform(0.3, 0.8) * acc["circle_use"]
                followers += [h.lower() for h in p.circle[pl] if rng.random() < u2]
            record = AccountRecord(
                platform=pl, handle=acc["handle"], platform_uid=f"{pl[:2]}-{p.pid}-{ai}",
                display_name=acc["display"], bio=acc["bio"],
                created_at_platform=EPOCH0 + timedelta(days=float(acc["created"])),
                followers=len(followers) or int(rng.integers(5, 3000)),
                following=len(following) or int(rng.integers(20, 1500)),
                avatar_phash=f"{acc['avatar']:016x}",
                following_handles=sorted(set(following)), follower_handles=sorted(set(followers)))
            prof = AccountProfile(account=record, posts=posts)
            profiles.append(prof)
            persona_of[prof.key] = p.pid
            meta[prof.key] = {"disguised": acc["disguised"], "archetype": p.archetype,
                              "n_accounts": len(p.accounts), "time_window": acc["window"],
                              "topics": acc["topics"], "english": p.idi.p_en > 0.5}
    order = rng.permutation(len(profiles))
    return SynthWorld(profiles=[profiles[i] for i in order], persona_of=persona_of, meta=meta, seed=seed)


def main() -> None:
    ap = argparse.ArgumentParser(description="Genera un mundo sintético de MENARD en JSON.")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--personas", type=int, default=60)
    ap.add_argument("--difficulty", type=float, default=1.0)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    w = generate_world(args.seed, args.personas, args.difficulty)
    payload = {
        "note": "Datos 100 % sintéticos generados por aleph.menard.synth; no corresponden a personas reales.",
        "seed": args.seed, "personas": args.personas, "difficulty": args.difficulty,
        "profiles": [p.model_dump(mode="json") for p in w.profiles],
        "truth": {k: {"persona": v, **w.meta[k]} for k, v in w.persona_of.items()},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"{len(w.profiles)} cuentas de {args.personas} personas → {args.out}")


if __name__ == "__main__":
    main()
