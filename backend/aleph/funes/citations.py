"""Verificación de citas: la defensa determinística contra alucinaciones.

Todo lo que propone el LLM tiene que traer una cita textual. Acá se busca esa cita en el texto
fuente; si no está, lo propuesto se descarta. La búsqueda tolera diferencias que no cambian el
contenido (espacios, mayúsculas, tildes, comillas tipográficas), y lo que se guarda como cita es
siempre el fragmento real del texto fuente, nunca lo que escribió el modelo.
"""

import re
import unicodedata
from dataclasses import dataclass

_QUOTE_CHARS = {"“": '"', "”": '"', "«": '"', "»": '"', "„": '"', "‘": "'", "’": "'", "`": "'",
                "´": "'", "–": "-", "—": "-", "‐": "-", "‑": "-", "…": "..."}
_EDGE_JUNK = " \t\r\n\"'“”«»‘’`.…,;:()[]"


def fold(text: str) -> str:
    """Forma de comparación: sin tildes, en minúscula, espacios colapsados."""
    return _normalize(text)[0].strip()


def _fold_char(ch: str) -> str:
    ch = _QUOTE_CHARS.get(ch, ch)
    decomposed = unicodedata.normalize("NFKD", ch)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _normalize(text: str) -> tuple[str, list[int]]:
    """Devuelve el texto plegado y, por cada carácter plegado, el índice original."""
    out: list[str] = []
    index: list[int] = []
    previous_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not previous_space:
                out.append(" ")
                index.append(i)
            previous_space = True
            continue
        folded = _fold_char(ch)
        if not folded:
            continue  # marca combinante suelta
        previous_space = False
        for piece in folded:
            out.append(piece)
            index.append(i)
    return "".join(out), index


@dataclass(frozen=True)
class QuoteMatch:
    start: int
    end: int
    text: str  # fragmento real del texto fuente
    exact: bool  # False si hubo que plegar tildes/mayúsculas/espacios para encontrarla


def locate_quote(source: str, quote: str, *, min_chars: int = 2) -> QuoteMatch | None:
    """Busca `quote` en `source`. None si no aparece."""
    if not source or not quote:
        return None
    wanted = quote.strip()
    if len(re.sub(r"\W", "", wanted)) < min_chars:
        return None
    pos = source.find(wanted)
    if pos != -1:
        return QuoteMatch(pos, pos + len(wanted), wanted, True)
    # Los modelos suelen agregar comillas o puntos suspensivos en los bordes.
    trimmed = wanted.strip(_EDGE_JUNK)
    if len(re.sub(r"\W", "", trimmed)) < min_chars:
        return None
    if trimmed != wanted:
        pos = source.find(trimmed)
        if pos != -1:
            return QuoteMatch(pos, pos + len(trimmed), trimmed, True)
    norm_source, index = _normalize(source)
    norm_quote = _normalize(trimmed)[0].strip()
    if not norm_quote:
        return None
    pos = norm_source.find(norm_quote)
    if pos == -1:
        return None
    start = index[pos]
    end = index[pos + len(norm_quote) - 1] + 1
    while end < len(source) and unicodedata.combining(source[end]):
        end += 1
    return QuoteMatch(start, end, source[start:end], False)


def contains_folded(haystack: str, needle: str) -> bool:
    needle = fold(needle)
    return bool(needle) and needle in fold(haystack)
