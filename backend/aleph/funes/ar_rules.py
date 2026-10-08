"""Reglas determinísticas para datos con forma fija en Argentina.

DNI, CUIT/CUIL (con dígito verificador), CBU/CVU (con verificación), patentes viejas y Mercosur,
teléfonos, emails, URLs, fechas en formatos locales y direcciones con altura.

Cada regla devuelve `RuleHit` con la cita textual y el offset. La confianza refleja cuánta forma
y contexto respaldan la lectura: un CUIT con dígito verificador correcto y la palabra "CUIT"
adelante vale más que once dígitos sueltos que casualmente verifican.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# --- Verificadores -------------------------------------------------------------------------

_CUIT_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
CUIT_PREFIXES_PERSON = ("20", "23", "24", "27")
CUIT_PREFIXES_COMPANY = ("30", "33", "34")
CUIT_PREFIXES = CUIT_PREFIXES_PERSON + CUIT_PREFIXES_COMPANY


def cuit_check_digit(first_ten: str) -> int | None:
    """Dígito verificador de un CUIT/CUIL. None si la combinación no admite verificador (resto 1)."""
    if len(first_ten) != 10 or not first_ten.isdigit():
        raise ValueError("se esperan 10 dígitos")
    remainder = sum(int(d) * w for d, w in zip(first_ten, _CUIT_WEIGHTS)) % 11
    if remainder == 0:
        return 0
    if remainder == 1:
        return None
    return 11 - remainder


def is_valid_cuit(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 11 or digits[:2] not in CUIT_PREFIXES:
        return False
    return cuit_check_digit(digits[:10]) == int(digits[10])


def _cbu_block_digit(block: str, weights: tuple[int, ...]) -> int:
    total = sum(int(d) * w for d, w in zip(block, weights))
    return (10 - total % 10) % 10


def cbu_check_digits(bank_branch: str, account: str) -> tuple[int, int]:
    """Verificadores de los dos bloques: 7 dígitos (entidad + sucursal) y 13 dígitos (cuenta)."""
    if len(bank_branch) != 7 or len(account) != 13 or not (bank_branch + account).isdigit():
        raise ValueError("se esperan 7 y 13 dígitos")
    return (
        _cbu_block_digit(bank_branch, (7, 1, 3, 9, 7, 1, 3)),
        _cbu_block_digit(account, (3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3)),
    )


def is_valid_cbu(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 22:
        return False
    first, second = cbu_check_digits(digits[:7], digits[8:21])
    return first == int(digits[7]) and second == int(digits[21])


# --- Resultados ----------------------------------------------------------------------------


@dataclass
class RuleHit:
    rule: str  # dni, cuit, cbu, patente, telefono, email, url, direccion
    type: str  # tipo de entidad de core/schemas.py
    label: str  # forma normalizada para mostrar
    value: str  # forma canónica para deduplicar
    start: int
    end: int
    quote: str
    confidence: float
    props: dict[str, Any] = field(default_factory=dict)


@dataclass
class DateHit:
    iso: str  # AAAA-MM-DD o AAAA-MM-DDTHH:MM
    start: int
    end: int
    quote: str
    has_time: bool = False
    two_digit_year: bool = False


def _context_before(text: str, pos: int, width: int = 30) -> str:
    return text[max(0, pos - width): pos]


class _Spans:
    """Tramos ya asignados: una misma cadena de dígitos no es a la vez CBU y teléfono."""

    def __init__(self) -> None:
        self._spans: list[tuple[int, int]] = []

    def free(self, start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in self._spans)

    def take(self, start: int, end: int) -> None:
        self._spans.append((start, end))


# --- URL y email ---------------------------------------------------------------------------

_RE_URL = re.compile(r"(?<![\w@])(?:https?://|www\.)[^\s<>\"'`{}|\\^\[\]]+", re.I)
_RE_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w-])"
)


def _find_urls(text: str) -> list[RuleHit]:
    hits = []
    for m in _RE_URL.finditer(text):
        raw = m.group(0).rstrip(".,;:!?)»”'\"")
        if len(raw) < 8 or "." not in raw:
            continue
        url = raw if raw.lower().startswith("http") else f"http://{raw}"
        host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].split("?")[0].lower()
        host = host.rsplit("@", 1)[-1].split(":")[0]
        hits.append(RuleHit("url", "url", url, url.lower().rstrip("/"), m.start(),
                            m.start() + len(raw), raw, 0.98, {"host": host}))
    return hits


def _find_emails(text: str) -> list[RuleHit]:
    hits = []
    for m in _RE_EMAIL.finditer(text):
        raw = m.group(0).rstrip(".")
        address = raw.lower()
        hits.append(RuleHit("email", "email", address, address, m.start(), m.start() + len(raw),
                            raw, 0.98, {"dominio": address.rsplit("@", 1)[1]}))
    return hits


# --- CBU / CVU -----------------------------------------------------------------------------

_RE_CBU = re.compile(r"(?<![\d-])\d{8}[ -]?\d{14}(?![\d-])")
_RE_CBU_KEYWORD = re.compile(r"\b(C\.?B\.?U|C\.?V\.?U)\b", re.I)


def _find_cbus(text: str) -> list[RuleHit]:
    hits = []
    for m in _RE_CBU.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        keyword = _RE_CBU_KEYWORD.search(_context_before(text, m.start()))
        valid = is_valid_cbu(digits)
        if not valid and not keyword:
            continue
        kind = "cvu" if keyword and "v" in keyword.group(1).lower() else "cbu"
        if valid:
            confidence = 0.99 if keyword else 0.95
        else:
            confidence = 0.3
        hits.append(RuleHit("cbu", "document", f"{kind.upper()} {digits}", f"cbu:{digits}",
                            m.start(), m.end(), m.group(0), confidence,
                            {"kind": kind, "numero": digits, "entidad": digits[:3],
                             "sucursal": digits[3:7], "verificador_valido": valid}))
    return hits


# --- CUIT / CUIL ---------------------------------------------------------------------------

_RE_CUIT = re.compile(r"(?<![\d-])(20|23|24|27|30|33|34)([-. ]?)(\d{8})\2(\d)(?![\d-])")
_RE_CUIT_KEYWORD = re.compile(r"\b(C\.?U\.?I\.?[TL])\b", re.I)


def _find_cuits(text: str) -> list[RuleHit]:
    hits = []
    for m in _RE_CUIT.finditer(text):
        prefix, separator, body, check = m.groups()
        digits = prefix + body + check
        keyword = _RE_CUIT_KEYWORD.search(_context_before(text, m.start()))
        valid = is_valid_cuit(digits)
        if not valid and not keyword:
            continue
        if not valid:
            confidence = 0.3
        elif keyword:
            confidence = 0.99
        elif separator in ("-", "."):
            confidence = 0.95
        else:  # once dígitos sueltos (o separados por espacio) que verifican
            confidence = 0.8
        kind = "cuit_cuil"
        if keyword:
            kind = "cuil" if keyword.group(1).lower().endswith("l") else "cuit"
        person = prefix in CUIT_PREFIXES_PERSON
        props: dict[str, Any] = {"kind": kind, "numero": digits, "verificador_valido": valid,
                                 "tipo_persona": "fisica" if person else "juridica"}
        if person:
            props["dni"] = body.lstrip("0")
        hits.append(RuleHit("cuit", "document", f"{kind.upper().replace('_', '/')} "
                            f"{prefix}-{body}-{check}", f"cuit:{digits}",
                            m.start(), m.end(), m.group(0), confidence, props))
    return hits


# --- DNI -----------------------------------------------------------------------------------

_DNI_NUMBER = r"\d{1,2}\.\d{3}\.\d{3}|\d{1,2} \d{3} \d{3}|\d{7,8}"
_RE_DNI_KEYWORD = re.compile(
    r"\b(?:D\.?\s?N\.?\s?I\.?|documento(?:\s+nacional\s+de\s+identidad)?)"
    r"\s*(?:n(?:ro|úmero|umero)?\.?\s*[°ºo]?\s*)?[:#]?\s*"
    rf"(?P<num>{_DNI_NUMBER})(?![\d.]\d|\d)",
    re.I,
)
_RE_DNI_DOTTED = re.compile(r"(?<![\d.,$])\d{1,2}\.\d{3}\.\d{3}(?![.,]?\d)")
_RE_MONEY_BEFORE = re.compile(r"(?:\$|\bpesos\b|\bd[oó]lares\b|\bUSD\b|\bARS\b|\bU\$S)\s*$", re.I)
_RE_MONEY_AFTER = re.compile(r"^\s*(?:pesos|d[oó]lares|USD|ARS|habitantes|personas|votos)\b", re.I)


def _dni_hit(text: str, start: int, end: int, number: str, confidence: float,
             keyword: bool) -> RuleHit | None:
    digits = re.sub(r"\D", "", number)
    if not 1_000_000 <= int(digits) <= 99_999_999:
        return None
    grouped = f"{int(digits):,}".replace(",", ".")
    return RuleHit("dni", "document", f"DNI {grouped}", f"dni:{int(digits)}", start, end,
                   text[start:end], confidence,
                   {"kind": "dni", "numero": str(int(digits)), "con_palabra_clave": keyword})


def _find_dnis(text: str) -> list[RuleHit]:
    hits = []
    for m in _RE_DNI_KEYWORD.finditer(text):
        hit = _dni_hit(text, m.start(), m.end("num"), m.group("num"), 0.95, True)
        if hit:
            hits.append(hit)
    for m in _RE_DNI_DOTTED.finditer(text):
        if _RE_MONEY_BEFORE.search(_context_before(text, m.start(), 12)):
            continue
        if _RE_MONEY_AFTER.search(text[m.end(): m.end() + 14]):
            continue
        hit = _dni_hit(text, m.start(), m.end(), m.group(0), 0.55, False)
        if hit:
            hits.append(hit)
    return hits


# --- Teléfonos -----------------------------------------------------------------------------

# Característica de 2 dígitos: solo AMBA. De 3: capitales y ciudades grandes. De 4: el resto.
_AREA_3 = ("220|221|223|230|236|237|249|260|261|263|264|266|280|291|294|297|298|299|"
           "336|341|342|343|345|348|351|353|358|362|364|370|376|379|380|381|383|385|387|388")
_AREAS = {2: "11", 3: f"(?:{_AREA_3})", 4: r"[23]\d{3}"}
_SEP = r"[ .-]?"


def _phone_regex(area_len: int) -> re.Pattern[str]:
    subscriber_len = 10 - area_len
    subscriber = r"\d" + rf"(?:{_SEP}\d)" * (subscriber_len - 1)
    return re.compile(
        r"(?<![\w+])(?<!\d[.,/-])"
        rf"(?:(?P<cc>\+?54){_SEP}(?P<m9>9{_SEP})?)?"
        rf"(?P<par>\()?(?P<zero>0)?(?P<area>{_AREAS[area_len]})\)?{_SEP}"
        rf"(?P<m15>15{_SEP})?"
        rf"(?P<sub>{subscriber})"
        r"(?!\d)(?![.,/-]\d)"
    )


_PHONE_REGEXES = [_phone_regex(n) for n in (2, 3, 4)]
_RE_PHONE_KEYWORD = re.compile(
    r"\b(?:tel[eé]fonos?|tel|cel|celular(?:es)?|m[oó]vil|whats?app|wsp|wpp|l[ií]nea|llam\w+|"
    r"contacto|abonado|n[uú]mero)\b[^\n]{0,12}$", re.I)


def _find_phones(text: str, spans: _Spans) -> list[RuleHit]:
    hits: list[RuleHit] = []
    local = _Spans()
    for pattern in _PHONE_REGEXES:
        for m in pattern.finditer(text):
            if not spans.free(m.start(), m.end()) or not local.free(m.start(), m.end()):
                continue
            raw = m.group(0)
            keyword = bool(_RE_PHONE_KEYWORD.search(_context_before(text, m.start())))
            strong = bool(m.group("cc") or m.group("zero") or m.group("m15") or keyword)
            has_separator = bool(re.search(r"\d[ .-]\d", raw))
            if strong:
                confidence = 0.9
            elif m.group("par"):
                confidence = 0.85
            elif has_separator:
                confidence = 0.5
            else:
                continue  # diez dígitos pegados y sin contexto: puede ser cualquier cosa
            mobile = bool(m.group("m9") or m.group("m15"))
            area = m.group("area")
            subscriber = re.sub(r"\D", "", m.group("sub"))
            e164 = f"+54{'9' if mobile else ''}{area}{subscriber}"
            local.take(m.start(), m.end())
            hits.append(RuleHit("telefono", "phone", e164, f"tel:{area}{subscriber}",
                                m.start(), m.end(), raw, confidence,
                                {"caracteristica": area, "abonado": subscriber,
                                 "movil": mobile, "e164": e164}))
    return hits


# --- Fechas --------------------------------------------------------------------------------

_MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
           "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11,
           "diciembre": 12}
_TIME = r"(?:(?:,)?\s+(?:a\s+las?\s+)?(?P<hh>\d{1,2})(?::|\.|\s?hs?\s?)(?P<mm>\d{2})(?!\d|[./-]\d))?"
_RE_DATE_NUMERIC = re.compile(
    r"(?<![\d/.-])(?P<d>\d{1,2})(?P<s>[/.-])(?P<m>\d{1,2})(?P=s)(?P<y>\d{4}|\d{2})(?![\d/-])" + _TIME)
_RE_DATE_ISO = re.compile(
    r"(?<![\d/.-])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?![\d/-])"
    r"(?:[T ](?P<hh>\d{2}):(?P<mm>\d{2})(?::\d{2})?)?")
_RE_DATE_TEXT = re.compile(
    r"(?<![\d/.-])(?P<d>\d{1,2})\s*[°º]?\s+de\s+(?P<mes>" + "|".join(_MONTHS) + r")"
    r"\s+(?:del?\s+)?(?P<y>\d{4})(?!\d)" + _TIME, re.I)


def _two_digit_year(year: int, pivot: int) -> int:
    return 2000 + year if year <= pivot else 1900 + year


def _build_date(m: re.Match[str], month: int, pivot: int) -> DateHit | None:
    year_text = m.group("y")
    year = int(year_text)
    short = len(year_text) == 2
    if short:
        year = _two_digit_year(year, pivot)
    try:
        day = date(year, month, int(m.group("d")))
    except ValueError:
        return None
    if not 1800 <= year <= 2100:
        return None
    iso = day.isoformat()
    end = m.end()
    has_time = False
    if m.group("hh") is not None:
        hour, minute = int(m.group("hh")), int(m.group("mm"))
        if hour < 24 and minute < 60:
            iso = f"{iso}T{hour:02d}:{minute:02d}"
            has_time = True
        else:
            end = m.start("hh")
            while end > m.start() and not m.string[end - 1].isdigit():
                end -= 1
    return DateHit(iso, m.start(), end, m.string[m.start():end], has_time, short)


def find_dates(text: str, *, two_digit_pivot: int | None = None) -> list[DateHit]:
    """Fechas en formatos locales (día primero), ISO y "12 de marzo de 2024"."""
    pivot = (date.today().year % 100) if two_digit_pivot is None else two_digit_pivot
    hits: list[DateHit] = []
    spans = _Spans()
    for pattern in (_RE_DATE_ISO, _RE_DATE_TEXT, _RE_DATE_NUMERIC):
        for m in pattern.finditer(text):
            if not spans.free(m.start(), m.end()):
                continue
            month = _MONTHS[m.group("mes").lower()] if "mes" in m.groupdict() else int(m.group("m"))
            if not 1 <= month <= 12:
                continue
            hit = _build_date(m, month, pivot)
            if hit:
                spans.take(hit.start, hit.end)
                hits.append(hit)
    return sorted(hits, key=lambda h: h.start)


def parse_local_date(value: str) -> datetime | None:
    """Interpreta una fecha suelta (ISO o formato local). None si no se entiende."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    hits = find_dates(value)
    if not hits:
        return None
    return datetime.fromisoformat(hits[0].iso)


# --- Patentes ------------------------------------------------------------------------------

_RE_PLATE_MERCOSUR = re.compile(r"(?<![\w-])([A-Z]{2})([ -]?)(\d{3})([ -]?)([A-Z]{2})(?![\w-])")
_RE_PLATE_OLD = re.compile(r"(?<![\w-])([A-Z]{3})([ -]?)(\d{3})(?![\w-])")
_RE_PLATE_KEYWORD = re.compile(r"\b(?:patentes?|dominios?|chapas?|matr[ií]culas?)\b[^\n]{0,25}$", re.I)
_RE_PLATE_KEYWORD_NEAR = re.compile(r"(?<!\w)(?:patente|dominio|chapa|matr[ií]cula)\W{0,3}$", re.I)
# Siglas de tres letras que aparecen pegadas a un número y no son patentes.
_PLATE_STOPWORDS = frozenset(
    "DNI LEY ART EXP RES DEC CBU CVU TEL CEL INT AÑO DEL LOS LAS CON POR SIN VER IVA USD ARS "
    "NRO NUM PAG FOJ FS CUI RUT RNR MAT DTO LIC UNA UNO QUE SON".split()
)


def _find_plates(text: str, spans: _Spans) -> list[RuleHit]:
    hits: list[RuleHit] = []
    local = _Spans()
    for pattern, fmt in ((_RE_PLATE_MERCOSUR, "mercosur"), (_RE_PLATE_OLD, "vieja")):
        for m in pattern.finditer(text):
            if not spans.free(m.start(), m.end()) or not local.free(m.start(), m.end()):
                continue
            keyword = bool(_RE_PLATE_KEYWORD.search(_context_before(text, m.start(), 40)))
            letters = m.group(1)
            if fmt == "vieja" and letters in _PLATE_STOPWORDS:
                # "LEY 123" solo es patente si la palabra clave está pegada ("patente LEY 123").
                if not _RE_PLATE_KEYWORD_NEAR.search(_context_before(text, m.start(), 14)):
                    continue
            plate = re.sub(r"[ -]", "", m.group(0))
            spaced = " " in m.group(0)
            if keyword:
                confidence = 0.95
            elif spaced:
                confidence = 0.5
            else:
                confidence = 0.85 if fmt == "mercosur" else 0.75
            local.take(m.start(), m.end())
            hits.append(RuleHit("patente", "vehicle", plate, f"patente:{plate}", m.start(),
                                m.end(), m.group(0), confidence,
                                {"kind": "patente", "formato": fmt, "patente": plate}))
    return hits


# --- Direcciones ---------------------------------------------------------------------------

_RE_ADDRESS = re.compile(
    r"(?<!\w)(?P<via>(?i:av(?:da)?\.?|avenida|calle|bv\.?|boulevard|bulevar|pje\.?|pasaje|"
    r"diag(?:onal)?\.?|camino))\s+"
    r"(?P<nombre>(?:(?:[A-ZÁÉÍÓÚÑÜ0-9][\wÁÉÍÓÚÑÜáéíóúñü.'’]*|de|del|la|las|los|y|e)\s+){1,6}?)"
    r"(?:(?:N[°ºro.]{1,3}|al)\s*)?(?P<altura>\d{1,5})(?![\d/]|[.,-]\d)"
    r"(?P<extra>(?:,?\s+(?:piso\s+\d{1,2}|\d{1,2}[°º]\s*(?:piso\s+)?[A-Z]?"
    r"|(?:dpto|depto|dto|departamento)\.?\s*\w{1,3}|PB\b))*)"
)


def _find_addresses(text: str) -> list[RuleHit]:
    hits = []
    for m in _RE_ADDRESS.finditer(text):
        name = " ".join(m.group("nombre").split())
        if not re.search(r"[^\W\d_]", name) and not re.fullmatch(r"\d{1,3}", name):
            continue
        quote = m.group(0).rstrip()
        label = " ".join(quote.split())
        hits.append(RuleHit("direccion", "location", label, f"dir:{label.lower()}", m.start(),
                            m.start() + len(quote), quote, 0.8,
                            {"kind": "direccion", "via": m.group("via"), "nombre": name,
                             "altura": m.group("altura"),
                             "complemento": " ".join(m.group("extra").strip(", ").split())}))
    return hits


# --- Orquestación --------------------------------------------------------------------------


def find_rule_hits(text: str) -> tuple[list[RuleHit], list[DateHit]]:
    """Corre todas las reglas. Las más específicas reclaman primero su tramo del texto."""
    spans = _Spans()
    hits: list[RuleHit] = []

    def claim(candidates: list[RuleHit]) -> None:
        for hit in candidates:
            if spans.free(hit.start, hit.end):
                spans.take(hit.start, hit.end)
                hits.append(hit)

    claim(_find_urls(text))
    claim(_find_emails(text))
    claim(_find_cbus(text))
    claim(_find_cuits(text))
    claim(_find_dnis(text))
    claim(_find_phones(text, spans))
    dates = [d for d in find_dates(text) if spans.free(d.start, d.end)]
    for found in dates:
        spans.take(found.start, found.end)
    claim(_find_plates(text, spans))
    claim(_find_addresses(text))
    return sorted(hits, key=lambda h: h.start), dates
