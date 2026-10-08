"""Control determinístico del lenguaje: FUNES estima, no afirma identidad ni culpabilidad."""

import re

from aleph.funes.citations import fold

# Se evalúan sobre texto plegado (sin tildes, minúscula).
_CATEGORICAL = [
    r"\bsin (?:lugar a |ninguna )?dudas?\b",
    r"\bcon (?:total |absoluta |plena )?certeza\b",
    r"\besta (?:confirmado|comprobado|probado|demostrado|acreditado)\b",
    r"\bqued[ao] (?:confirmado|comprobado|probado|demostrado|acreditado)\b",
    r"\bse (?:confirmo|comprobo|demostro|probo|acredito)\b",
    r"\b(?:es|son|fue|fueron) (?:el |la |los |las )?(?:culpables?|autor(?:es|a|as)?|responsables?)\b",
    r"\b(?:es|son|se trata de) (?:la misma persona|el mismo individuo|el mismo operador)\b",
    r"\bcometio\b",
    r"\b(?:indudablemente|definitivamente|inequivocamente|indiscutiblemente|irrefutablemente)\b",
    r"\bno (?:hay|cabe|caben|quedan) dudas?\b",
]
_RE_CATEGORICAL = [re.compile(p) for p in _CATEGORICAL]
_RE_NEGATION = re.compile(r"\b(?:no|ni|nunca|tampoco)\b[^.;]{0,40}$")


def find_categorical(text: str) -> list[str]:
    """Frases que afirman identidad, culpabilidad o certeza. Lista vacía si el texto estima."""
    folded = fold(text)
    found: list[str] = []
    for pattern in _RE_CATEGORICAL:
        for match in pattern.finditer(folded):
            # "no se puede afirmar que es la misma persona" no es una afirmación categórica.
            before = folded[max(0, match.start() - 60): match.start()]
            if _RE_NEGATION.search(before) and not match.group(0).startswith("no "):
                continue
            found.append(match.group(0))
    return found
