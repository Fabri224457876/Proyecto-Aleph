"""Extracción de rasgos por cuenta. Se calcula una sola vez por cuenta y se reutiliza en todos los pares."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlparse

import numpy as np

from aleph.core.schemas import AccountProfile

from . import lexicon as lx

NGRAM_SIZES = (3, 4)
_PRIMES = np.array([1000003, 998244353, 1000000007, 4294967311, 6700417], dtype=np.uint64)

# Etiquetas legibles de cada dimensión de hábito; "pct" = proporción, "num" = número.
HABIT_LABELS: dict[str, list[tuple[str, str]]] = {
    "punct": [
        ("mensajes que terminan con punto final", "pct"),
        ("mensajes que terminan sin ningún signo", "pct"),
        ("mensajes con puntos suspensivos", "pct"),
        ("mensajes con exclamación repetida (!!)", "pct"),
        ("mensajes con pregunta repetida (??)", "pct"),
        ("preguntas abiertas con ¿", "pct"),
        ("exclamaciones abiertas con ¡", "pct"),
        ("comas cada 100 caracteres", "num"),
        ("puntos cada 100 caracteres", "num"),
        ("exclamaciones cada 100 caracteres", "num"),
        ("preguntas cada 100 caracteres", "num"),
        ("dos puntos / punto y coma cada 100 caracteres", "num"),
        ("paréntesis cada 100 caracteres", "num"),
        ("comillas cada 100 caracteres", "num"),
        ("guiones cada 100 caracteres", "num"),
        ("signos precedidos por un espacio", "pct"),
        ("comas sin espacio después", "pct"),
    ],
    "caps": [
        ("mensajes que empiezan con mayúscula", "pct"),
        ("palabras enteras EN MAYÚSCULAS", "pct"),
        ("mensajes completamente en minúsculas", "pct"),
        ("mayúscula después de un punto", "pct"),
    ],
    "elong": [
        ("mensajes con palabras alargadas (\"holaaa\")", "pct"),
        ("mensajes con risas escritas", "pct"),
        ("largo medio de la risa (caracteres)", "num"),
        ("risas en mayúsculas", "pct"),
        ("alargamientos sobre vocal (y no consonante)", "pct"),
    ]
    + [(f"risa tipo «{fam}»", "pct") for fam in lx.LAUGH_FAMILIES],
    "emoji": [
        ("emojis por mensaje", "num"),
        ("mensajes con emoji", "pct"),
        ("emoji al final del mensaje", "pct"),
        ("mismo emoji repetido seguido", "pct"),
        ("mensajes con emoticones de texto", "pct"),
    ],
    "length": [
        ("largo típico del mensaje (log de caracteres)", "num"),
        ("variabilidad del largo del mensaje", "num"),
        ("palabras por frase (log)", "num"),
        ("mensajes con más de una frase", "pct"),
        ("mensajes de tres palabras o menos", "pct"),
        ("largo medio de palabra", "num"),
    ],
    "mix": [
        ("proporción de respuestas", "pct"),
        ("proporción de reposts", "pct"),
        ("proporción de citas", "pct"),
        ("enlaces por publicación", "num"),
        ("hashtags por publicación", "num"),
        ("menciones por publicación", "num"),
    ],
    "rio": [(f"marca «{d}» cada 100 palabras", "num") for d in lx.RIO_DIMS],
    "ortho": [(f"«{v}» en lugar de «{c}»", "pct") for c, v in lx.VARIANT_DIMS]
    + [("tildes omitidas", "pct")],
    "fw": [(f"frecuencia de «{w}» (cada 100 palabras)", "num") for w in lx.FUNCTION_WORDS],
}
HABIT_DIMS = {g: len(v) for g, v in HABIT_LABELS.items()}
_VARIANT_DIM_INDEX = {cv: i for i, cv in enumerate(lx.VARIANT_DIMS)}


@dataclass
class AccountFeatures:
    key: str
    platform: str
    handle: str
    n_posts: int
    n_authored: int
    n_tokens: int
    texts: list[str]
    ngram_ids: np.ndarray
    ngram_counts: np.ndarray
    habits: dict[str, np.ndarray]
    sets: dict[str, Counter] = field(default_factory=dict)
    times: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    handle_norm: str = ""
    handle_radical: str = ""
    handle_alt: str = ""
    display_norm: str = ""
    bio: str = ""
    created_days: float = math.nan
    phash: int | None = None
    phash_bits: int = 0
    lang_es: float = 0.5


def clean_text(text: str) -> str:
    """Quita URLs y neutraliza menciones y hashtags (son tema o destinatario, no estilo)."""
    t = lx.URL_RE.sub(" ", text)
    t = lx.MENTION_RE.sub("@", t)
    t = lx.HASHTAG_RE.sub("#", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def ngram_hashes(text: str) -> np.ndarray:
    """Hashes de 64 bits de los n-gramas de caracteres de un texto."""
    cp = np.frombuffer(text.encode("utf-32-le"), dtype=np.uint32).astype(np.uint64)
    out = []
    for n in NGRAM_SIZES:
        if cp.size < n:
            continue
        h = np.full(cp.size - n + 1, n, dtype=np.uint64) * np.uint64(0x9E3779B97F4A7C15)
        for k in range(n):
            h = h + cp[k : cp.size - n + 1 + k] * _PRIMES[k]
            h = h * np.uint64(0x100000001B3)
        out.append(h)
    return np.concatenate(out) if out else np.zeros(0, dtype=np.uint64)


def ngram_strings(text: str) -> dict[int, str]:
    """Mapa hash -> n-grama, solo para armar evidencia de un par concreto."""
    hs = ngram_hashes(text)
    out: dict[int, str] = {}
    pos = 0
    for n in NGRAM_SIZES:
        cnt = len(text) - n + 1
        if cnt <= 0:
            continue
        for i in range(cnt):
            out[int(hs[pos + i])] = text[i : i + n]
        pos += cnt
    return out


def _frac(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else math.nan


def _ratio(num: float, den: float, min_den: float, cap: float = math.inf) -> float:
    return min(num / den, cap) if den >= min_den and den > 0 else math.nan


def _deleet(s: str) -> str:
    return re.sub(r"[^a-zñ]", "", lx.strip_accents(s.translate(lx.LEET)))


def normalize_handle(handle: str) -> tuple[str, str, str]:
    """Devuelve (handle en minúscula, radical, radical alternativo).

    El radical descarta sufijos numéricos ("nico_dev_87" → "nicodev") y deshace el leetspeak
    ("n1c0" → "nico"). Como un dígito final puede ser sufijo o letra disfrazada, el alternativo
    traduce todos los dígitos; la señal de handle se queda con la mejor coincidencia."""
    norm = handle.lower().lstrip("@")
    trimmed = re.sub(r"(?:(?<=[^\d])\d{2,}|(?<=[\W_])\d+)$", "", norm)
    trimmed = re.sub(r"(?<![a-zñ\d])\d+(?![a-zñ\d])", "", trimmed)
    return norm, _deleet(trimmed), _deleet(norm)


def _domain(url: str) -> str:
    try:
        host = urlparse(url if "//" in url else "//" + url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def extract(profile: AccountProfile) -> AccountFeatures:
    acc = profile.account
    posts = profile.posts
    authored = [clean_text(p.text) for p in posts if p.kind != "repost" and p.text]
    texts = [t for t in authored if t]
    n = len(texts)
    joined = "\n".join(texts)
    habits: dict[str, np.ndarray] = {}
    sets: dict[str, Counter] = {}

    # --- n-gramas de caracteres -------------------------------------------------------------
    if joined:
        ids, counts = np.unique(ngram_hashes("\n" + joined + "\n"), return_counts=True)
    else:
        ids, counts = np.zeros(0, dtype=np.uint64), np.zeros(0, dtype=np.int64)

    # --- tokens ------------------------------------------------------------------------------
    words_orig = lx.WORD_RE.findall(joined)
    toks = [w.lower() for w in words_orig]
    n_tok = len(toks)
    tok_counts = Counter(toks)
    tokna_counts: Counter = Counter()
    for w, cnt in tok_counts.items():
        tokna_counts[lx.strip_accents(w)] += cnt

    fw = np.zeros(len(lx.FUNCTION_WORDS))
    for w, c in tokna_counts.items():
        i = lx.FW_INDEX.get(w)
        if i is not None:
            fw[i] = c
    habits["fw"] = fw * (100.0 / n_tok) if n_tok >= 30 else np.full(len(fw), np.nan)
    es = sum(c for w, c in tokna_counts.items() if w in lx.ES_HINT)
    en = sum(c for w, c in tokna_counts.items() if w in lx.EN_HINT)
    lang_es = (es + 1) / (es + en + 2)

    # --- puntuación --------------------------------------------------------------------------
    nch = max(len(joined), 1)
    per100 = 100.0 / nch
    nq, ne, ncomma = joined.count("?"), joined.count("!"), joined.count(",")
    marks = len(re.findall(r"[,.!?;:]", joined))
    ell = joined.count("...") + joined.count("…")
    if n:
        habits["punct"] = np.array([
            _frac([t.endswith(".") and not t.endswith("..") for t in texts]),
            _frac([t[-1].isalnum() for t in texts]),
            _frac([("..." in t or "…" in t) for t in texts]),
            _frac(["!!" in t for t in texts]),
            _frac(["??" in t for t in texts]),
            _ratio(joined.count("¿"), nq, 2, 1.0),
            _ratio(joined.count("¡"), ne, 2, 1.0),
            ncomma * per100,
            max(joined.count(".") - 3 * joined.count("..."), 0) * per100,
            ne * per100,
            nq * per100,
            (joined.count(":") + joined.count(";")) * per100,
            (joined.count("(") + joined.count(")")) * per100,
            (joined.count('"') + joined.count("«") + joined.count("“")) * per100,
            (joined.count(" - ") + joined.count("—")) * per100,
            _ratio(len(re.findall(r"[^\W\d_] [,.!?;:]", joined)), marks, 3),
            _ratio(len(re.findall(r",[^\s\d]", joined)), ncomma, 2),
        ])
    else:
        habits["punct"] = np.full(HABIT_DIMS["punct"], np.nan)

    # --- mayúsculas --------------------------------------------------------------------------
    laughs = lx.LAUGH_RE.findall(joined)
    laugh_set = set(laughs)
    starts = [t[0].isupper() for t in texts if t[0].isalpha()]
    after = re.findall(r"[.!?]\s+([^\W\d_])", joined)
    caps_words = sum(1 for w in words_orig if len(w) >= 2 and w.isupper() and w not in laugh_set)
    habits["caps"] = np.array([
        _ratio(sum(starts), len(starts), 2),
        _ratio(caps_words, n_tok, 20),
        _frac([t == t.lower() for t in texts]),
        _ratio(sum(c.isupper() for c in after), len(after), 2),
    ])

    # --- alargamientos y risas ---------------------------------------------------------------
    elong_posts, laugh_posts, elong_chars = 0, 0, []
    for t in texts:
        has_l = lx.LAUGH_RE.search(t) is not None
        laugh_posts += has_l
        rest = lx.LAUGH_RE.sub(" ", t) if has_l else t
        m = [x for x in lx.ELONG_RE.findall(rest)]
        if m:
            elong_posts += 1
            elong_chars.extend(m)
    fam = Counter(lx.laugh_family(x) for x in laughs)
    nl = len(laughs)
    habits["elong"] = np.array(
        [
            _ratio(elong_posts, n, 1),
            _ratio(laugh_posts, n, 1),
            _ratio(sum(len(x) for x in laughs), nl, 2),
            _ratio(sum(x.isupper() for x in laughs), nl, 2),
            _ratio(sum(c.lower() in lx.VOWELS for c in elong_chars), len(elong_chars), 2),
        ]
        + [_ratio(fam.get(f, 0), nl, 2) for f in lx.LAUGH_FAMILIES]
    )
    sets["laugh"] = Counter(x.lower()[:8] for x in laughs)

    # --- emojis ------------------------------------------------------------------------------
    emo = Counter()
    emo_posts = end_emo = rep_emo = emot_posts = 0
    for t in texts:
        found = lx.EMOJI_RE.findall(t)
        emots = lx.EMOTICON_RE.findall(t)
        if emots:
            emot_posts += 1
            emo.update(emots)
        if found:
            emo_posts += 1
            emo.update(found)
            tail = t.rstrip("️‍ ")
            end_emo += bool(lx.EMOJI_RE.match(tail[-1]))
            rep_emo += any(a == b for a, b in zip(found, found[1:]))
    n_emoji = sum(c for k, c in emo.items() if lx.EMOJI_RE.match(k))
    habits["emoji"] = np.array([
        _ratio(n_emoji, n, 1),
        _ratio(emo_posts, n, 1),
        _ratio(end_emo, emo_posts, 2),
        _ratio(rep_emo, emo_posts, 2),
        _ratio(emot_posts, n, 1),
    ])
    sets["emoji"] = emo

    # --- longitudes --------------------------------------------------------------------------
    if n:
        logc = np.log1p([len(t) for t in texts])
        sents = [[s for s in lx.SENT_SPLIT_RE.split(t) if s.strip()] for t in texts]
        wps = [len(lx.WORD_RE.findall(s)) for ss in sents for s in ss]
        wpp = [len(lx.WORD_RE.findall(t)) for t in texts]
        habits["length"] = np.array([
            float(logc.mean()),
            float(logc.std()) if n >= 3 else math.nan,
            float(np.mean(np.log1p(wps))) if wps else math.nan,
            _frac([len(ss) > 1 for ss in sents]),
            _frac([w <= 3 for w in wpp]),
            _ratio(sum(len(w) for w in toks), n_tok, 10),
        ])
    else:
        habits["length"] = np.full(HABIT_DIMS["length"], np.nan)

    # --- mezcla de tipos de publicación ------------------------------------------------------
    np_all = len(posts)
    kinds = Counter(p.kind for p in posts)
    habits["mix"] = np.array([
        _ratio(kinds.get("reply", 0), np_all, 1),
        _ratio(kinds.get("repost", 0), np_all, 1),
        _ratio(kinds.get("quote", 0), np_all, 1),
        _ratio(sum(len(p.urls) for p in posts), np_all, 1),
        _ratio(sum(len(p.hashtags) for p in posts), np_all, 1),
        _ratio(sum(len(p.mentions) for p in posts), np_all, 1),
    ])

    # --- marcas rioplatenses -----------------------------------------------------------------
    if n_tok >= 40 and lang_es >= 0.3:
        habits["rio"] = np.array([
            100.0 * sum(tok_counts.get(w, 0) for w in s) / n_tok for s in lx.RIO_SETS.values()
        ])
    else:
        habits["rio"] = np.full(HABIT_DIMS["rio"], np.nan)

    # --- ortografía: variantes, abreviaturas, tildes -----------------------------------------
    group_tot: Counter = Counter()
    for w, c in tokna_counts.items():
        g = lx.VARIANT_LOOKUP.get(w)
        if g:
            group_tot[g] += c
    group_tot["igual"] += joined.count(" = ")
    ortho = np.full(HABIT_DIMS["ortho"], np.nan)
    for (canon, var), i in _VARIANT_DIM_INDEX.items():
        cnt = joined.count(" = ") if var == "=" else tokna_counts.get(var, 0)
        ortho[i] = _ratio(cnt, group_tot.get(canon, 0), 2)
    omitted = accented = 0
    for w, c in tok_counts.items():
        na = lx.strip_accents(w)
        if na in lx.ACCENT_WORDS or (len(na) > 5 and lx.ACCENT_SUFFIX_RE.search(na)):
            if na == w:
                omitted += c
            else:
                accented += c
    ortho[-1] = _ratio(omitted, omitted + accented, 3)
    habits["ortho"] = ortho
    sets["misspell"] = Counter({w: c for w, c in tok_counts.items() if w in lx.MISSPELL_TOKENS})
    sets["words"] = tok_counts

    # --- conducta ----------------------------------------------------------------------------
    sets["hashtags"] = Counter(h.lower().lstrip("#") for p in posts for h in p.hashtags if h)
    sets["domains"] = Counter(d for p in posts for u in p.urls if (d := _domain(u)))
    targets: Counter = Counter()
    for p in posts:
        if p.reply_to:
            targets[p.reply_to.lower().lstrip("@")] += 1
        for m in p.mentions:
            targets[m.lower().lstrip("@")] += 1
    sets["targets"] = targets
    clients: Counter = Counter()
    for p in posts:
        if p.client:
            clients[p.client.strip().lower()] += 1
            f = lx.client_family(p.client)
            if f:
                clients["familia:" + f] += 1
    sets["clients"] = clients
    sets["following"] = Counter({h.lower().lstrip("@"): 1 for h in acc.following_handles if h})
    sets["followers"] = Counter({h.lower().lstrip("@"): 1 for h in acc.follower_handles if h})

    # --- tiempos -----------------------------------------------------------------------------
    times = np.array(
        sorted(int(p.created_at.timestamp()) for p in posts if p.created_at is not None),
        dtype=np.int64,
    )

    # --- perfil ------------------------------------------------------------------------------
    norm, radical, alt = normalize_handle(acc.handle)
    phash, bits = None, 0
    if acc.avatar_phash:
        try:
            phash, bits = int(acc.avatar_phash, 16), 4 * len(acc.avatar_phash)
        except ValueError:
            phash, bits = None, 0
    created = acc.created_at_platform.timestamp() / 86400.0 if acc.created_at_platform else math.nan

    return AccountFeatures(
        key=profile.key, platform=acc.platform.lower(), handle=acc.handle, n_posts=np_all,
        n_authored=n, n_tokens=n_tok, texts=texts, ngram_ids=ids, ngram_counts=counts,
        habits=habits, sets=sets, times=times, handle_norm=norm, handle_radical=radical, handle_alt=alt,
        display_norm=lx.strip_accents(acc.display_name.lower()).strip(), bio=acc.bio.strip(),
        created_days=created, phash=phash, phash_bits=bits, lang_es=lang_es,
    )
