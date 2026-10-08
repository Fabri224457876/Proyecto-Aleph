"""Recursos léxicos y expresiones regulares de MENARD (español rioplatense + inglés)."""

from __future__ import annotations

import re
import unicodedata

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
MENTION_RE = re.compile(r"@\w+")
HASHTAG_RE = re.compile(r"#\w+")
WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿←-⇿⌀-⏿]"
)
EMOTICON_RE = re.compile(
    r"(?<![\w/])(?:[:;=]['\-]?[)(DPp3/\\Oo*|]+|<3+|\^\^|\^_\^|-_-|:c|:v|u_u|uwu|owo|:'\()(?!\w)"
)
LAUGH_RE = re.compile(
    r"\b(?:a?(?:j[aeiks]+){2,}j?|a?(?:ja){2,}j?|(?:h[ae]){2,}h?|lo+l+|x+d+|k{3,}|lmao+|lmfao)\b",
    re.I,
)
ELONG_RE = re.compile(r"([^\W\d_])\1{2,}")
VOWELS = set("aeiouáéíóú")


_ACCENT_TABLE = str.maketrans("áéíóúüàèìòùâêîôûäëïöÁÉÍÓÚÜÀÈÌÒÙÂÊÎÔÛÄËÏÖ",
                               "aeiouuaeiouaeiouaeioAEIOUUAEIOUAEIOUAEIO")


def strip_accents(s: str) -> str:
    """Quita tildes pero conserva la ñ."""
    if s.isascii():
        return s
    s = s.translate(_ACCENT_TABLE)
    if s.isascii() or all(ch in "ñÑ" or ord(ch) < 128 or not unicodedata.decomposition(ch) for ch in s):
        return s
    out = []
    for ch in s:
        if ch in "ñÑ":
            out.append(ch)
            continue
        d = unicodedata.normalize("NFD", ch)
        out.append("".join(c for c in d if unicodedata.category(c) != "Mn"))
    return "".join(out)


def laugh_family(s: str) -> str:
    s = s.lower()
    if s.startswith("lo") or s.startswith("lm"):
        return "lol"
    if s.startswith("x"):
        return "xd"
    if s.startswith("k"):
        return "kkk"
    if s[0] == "h":
        return "ha"
    if "s" in s or "k" in s:
        return "jsjs"
    if s[0] == "a":
        return "ajaj"
    if "e" in s and "a" not in s:
        return "je"
    if "i" in s and "a" not in s:
        return "ji"
    return "ja"


LAUGH_FAMILIES = ("ja", "je", "ji", "ha", "jsjs", "ajaj", "lol", "xd", "kkk")

_FW_ES = """de la que el en y a los se del las un por con no una su para es al lo como mas pero
sus le ya o fue este ha si porque esta son entre cuando muy sin sobre ser tiene tambien me hasta
hay donde han quien estan desde todo nos durante uno les ni contra otros ese eso habia ante ellos
e esto mi antes algunos unos yo otro otras otra el tanto esa estos mucho quienes nada muchos cual
sea poco ella estar haber estas algunas algo nosotros mis tu te ti tus vos igual bueno entonces
despues ahora siempre nunca aca ahi alla asi bien mal casi capaz obvio literal tipo onda nomas
encima ademas aunque mientras pues luego tampoco todavia aun quizas apenas igualmente total
medio tan tal cada menos hace hoy ayer aunque sino solo toda todos todas nadie alguien""".split()
_FW_EN = """the of and to in is it you that he was for on are with as i his they be at one have
this from or had by but what some we can out other were all there when up your how said an each
she which do their if will way about many then them would like so these her him has more could
my than been who its now did get just not really very actually literally also though because
still even only too me am im i'm don't dont it's that's gonna wanna kinda yeah nah""".split()
FUNCTION_WORDS: tuple[str, ...] = tuple(dict.fromkeys(_FW_ES + _FW_EN))
FW_INDEX = {w: i for i, w in enumerate(FUNCTION_WORDS)}
ES_HINT = set(_FW_ES) - set(_FW_EN)
EN_HINT = set(_FW_EN) - set(_FW_ES)

# Grupos de variantes: forma canónica -> abreviaturas / grafías alternativas.
# Se mide la proporción con que la cuenta elige cada variante cuando tuvo la oportunidad.
VARIANT_GROUPS: dict[str, tuple[str, ...]] = {
    "que": ("q", "k", "ke", "qe"),
    "porque": ("xq", "pq", "porq", "xk", "xke"),
    "por": ("x",),
    "para": ("pa", "xa"),
    "de": ("d",),
    "tambien": ("tmb", "tb", "tmbn"),
    "bien": ("bn",),
    "estoy": ("toy",),
    "esta": ("ta",),
    "gracias": ("grax", "grac", "gcs"),
    "mensaje": ("msj",),
    "despues": ("dsp", "desp"),
    "nada": ("nd",),
    "mucho": ("mcho", "muxo"),
    "bueno": ("weno", "bno"),
    "tampoco": ("tmp", "tmpc"),
    "igual": ("=", "igualmente"),
    "you": ("u",),
    "your": ("ur",),
    "are": ("r",),
    "please": ("pls", "plz"),
    "thanks": ("thx", "ty", "thnx"),
    "because": ("bc", "cuz", "cos"),
    "though": ("tho",),
    "people": ("ppl",),
    "don't": ("dont",),
    "i'm": ("im",),
    "can't": ("cant",),
    "that's": ("thats",),
}
VARIANT_DIMS: list[tuple[str, str]] = [
    (canon, v) for canon, vs in VARIANT_GROUPS.items() for v in vs if v != "igualmente"
]
VARIANT_LOOKUP: dict[str, str] = {}
for _c, _vs in VARIANT_GROUPS.items():
    VARIANT_LOOKUP[_c] = _c
    for _v in _vs:
        if _v != "igualmente":
            VARIANT_LOOKUP[_v] = _c

# Grafías recurrentes no estándar (se comparan como conjunto ponderado por rareza).
MISSPELL_TOKENS = frozenset(
    """osea enserio aveces talvez derrepente nose haci aser iva valla halla porfa xfa porfi tqm
    ntp nvm finde kiero ksa weno wena bue sep nop sip see ahre haber ay ves tubo echo asta ahy
    ola aki ke kien kiere komo nah yep idk tbh imo rn omg wtf smh fr ngl btw irl af
    alot definately recieve seperate untill wich thier becuase""".split()
)

# Palabras cuya forma sin tilde no es otra palabra frecuente: sirven para medir omisión de tildes.
ACCENT_WORDS = frozenset(
    """tambien despues ademas todavia aca ahi alla asi dia dias facil dificil musica ultimo ultima
    numero rapido jamas quizas segun tenia habia podia queria estan mama cafe aqui alli ningun
    algun ojala razon corazon cancion increible pelicula politica publico unico pais mas tenes
    queres podes entendes venis decis pensas decia
    arbol futbol atras detras ingles frances interes proximo proxima sabado miercoles telefono
    pagina codigo credito basico clasico tipico logico magico""".split()
)
ACCENT_SUFFIX_RE = re.compile(r"(?:c|s)i[oó]n$")

VOSEO = frozenset(
    """tenés querés podés sabés sos venís decís hacés pensás creés mirá fijate decime contame andá
    vení imaginate escuchá pará esperá avisame pasame mandame tomá hacé poné salí entendés
    tenes queres podes venis decis pensas veni sali entendes hacete ponete quedate andate
    acordate olvidate sentís sentis vivís seguís pedís contás llamás buscás""".split()
)
TUTEO = frozenset(
    "tienes quieres puedes eres vienes dices piensas tú contigo entiendes sientes mira oye".split()
)
RIO_SETS: dict[str, frozenset[str]] = {
    "voseo": VOSEO,
    "tuteo": TUTEO,
    "che": frozenset({"che"}),
    "re": frozenset({"re"}),
    "vos": frozenset({"vos"}),
    "boludo": frozenset("boludo boluda bolu boludx gil salame pelotudo pelotuda".split()),
    "tipo": frozenset({"tipo"}),
    "posta": frozenset({"posta"}),
    "dale": frozenset({"dale"}),
    "viste": frozenset({"viste"}),
    "lunfardo": frozenset(
        """laburo laburar guita bondi pibe piba pibes mina chabon chabón quilombo birra morfar
        morfi copado copada zarpado zarpada flashear flasheo flashero joya fiaca garpa mango
        mangos trucho trucha chamuyo bardo bardear cheto cheta groso grosa capo""".split()
    ),
    "peninsular": frozenset("vale tío tía mola guay joder vosotros hostia chaval".split()),
    "otras_variedades": frozenset(
        "wey güey chido neta órale pinche parce chévere weon wn bacán pana chamo".split()
    ),
}
RIO_DIMS = tuple(RIO_SETS)

LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a",
                      "$": "s", "8": "b"})


def client_family(client: str) -> str:
    c = client.lower()
    for fam, keys in (
        ("android", ("android",)),
        ("iphone", ("iphone", "ios", "ipad")),
        ("web", ("web", "browser")),
        ("escritorio", ("deck", "desktop", "mac", "windows")),
        ("automatizado", ("bot", "ifttt", "buffer", "hootsuite", "zapier", "api")),
    ):
        if any(k in c for k in keys):
            return fam
    return ""
