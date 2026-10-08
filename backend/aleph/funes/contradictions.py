"""Motor de contradicciones entre fuentes.

La detección es determinística y no usa el LLM:
  (a) imposibilidad espacio-temporal: un mismo sujeto en dos lugares sin tiempo de traslado
      viable (distancia haversine contra una velocidad máxima configurable);
  (b) valores incompatibles para atributos de valor único (fecha de nacimiento, DNI…);
  (c) intervalos que se excluyen (dos estados incompatibles al mismo tiempo).

El LLM solo se usa para (1) convertir texto en afirmaciones, con cita obligatoria verificada
contra el texto fuente, y (2) redactar en español la explicación de una contradicción ya
detectada. Cada contradicción lista las fuentes, la severidad y las lecturas posibles, sin
elegir una: decide el analista.
"""

import math
import re
from datetime import UTC, datetime, timedelta, timezone
from itertools import combinations
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from aleph.funes import prompts
from aleph.funes.ar_rules import parse_local_date
from aleph.funes.citations import fold, locate_quote
from aleph.funes.client import FunesClient, LLMError, split_text
from aleph.funes.language import find_categorical
from aleph.funes.structured import Confidence, LooseStr, StructuredOutputError, lenient_list

EARTH_RADIUS_KM = 6371.0088
Severity = Literal["baja", "media", "alta"]
ContradictionKind = Literal["espacio_temporal", "valor_unico", "intervalos_excluyentes"]

# --- Tipos ---------------------------------------------------------------------------------


class Place(BaseModel):
    name: str = ""
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)

    @property
    def has_coords(self) -> bool:
        return self.lat is not None and self.lon is not None


class Claim(BaseModel):
    """Una afirmación de una fuente sobre un sujeto.

    Tiempo: `start` solo es un instante; `start` y `end` son un intervalo. `time_mode` dice cómo
    leer el intervalo: "durante" (el hecho vale todo el intervalo) o "dentro_de" (ocurrió en algún
    momento del intervalo, p. ej. "el 10 de marzo" sin hora). `open_end` marca un estado que
    sigue vigente. Las fechas sin zona horaria se toman como UTC.
    """

    id: str
    subject: str  # id o nombre del sujeto; quien llama resuelve que dos nombres son el mismo
    predicate: str  # "ubicado_en", "fecha_nacimiento", "dni", "detenido", …
    value: str = ""
    start: datetime | None = None
    end: datetime | None = None
    time_mode: Literal["durante", "dentro_de"] = "durante"
    open_end: bool = False
    place: Place | None = None
    source: str = ""  # id de la fuente
    quote: str = ""
    offset: int | None = None
    confidence: float = 1.0
    props: dict[str, Any] = Field(default_factory=dict)


class Reading(BaseModel):
    """Una lectura posible de la contradicción. Son alternativas; ninguna es la elegida."""

    kind: str
    description: str


class Contradiction(BaseModel):
    id: str
    kind: ContradictionKind
    severity: Severity
    subject: str
    claim_ids: list[str]
    sources: list[str]  # fuentes en conflicto
    summary: str  # redacción determinística, siempre presente
    details: dict[str, Any] = Field(default_factory=dict)
    readings: list[Reading] = Field(default_factory=list)
    explanation: str = ""  # redacción ampliada (LLM, o la determinística si el LLM no sirve)
    explanation_source: Literal["", "llm", "plantilla"] = ""


class ContradictionConfig(BaseModel):
    max_speed_kmh: float = Field(900.0, gt=0)  # vuelo comercial; bajar para casos terrestres
    min_distance_km: float = Field(2.0, ge=0)  # por debajo se considera el mismo lugar
    time_slack_minutes: float = Field(0.0, ge=0)  # margen por imprecisión de los horarios
    presence_predicates: set[str] = {"ubicado_en", "visto_en", "presente_en", "estuvo_en",
                                     "publico_desde", "detenido_en", "internado_en"}
    single_valued_predicates: set[str] = {"fecha_nacimiento", "dni", "cuit", "cuil",
                                          "lugar_nacimiento", "fecha_fallecimiento"}
    # Mismo predicado con valores distintos a la vez: no se puede estar detenido en dos lugares.
    exclusive_value_predicates: set[str] = {"detenido_en", "internado_en"}
    # Estados que no pueden valer a la vez para un mismo sujeto.
    exclusive_groups: list[set[str]] = [{"detenido", "en_libertad"}, {"vivo", "fallecido"},
                                        {"detenido_en", "en_libertad"}]


READINGS: dict[str, str] = {
    "suplantacion": "Suplantación o uso por terceros: otra persona usa la identidad, la cuenta "
                    "o el documento del sujeto.",
    "error_de_carga": "Error de carga o de registro: fecha, hora, huso horario, lugar o dígitos "
                      "mal consignados en alguna de las fuentes.",
    "homonimos": "Homónimos: las fuentes hablan de dos personas distintas que quedaron "
                 "fusionadas en un mismo sujeto.",
    "fuente_poco_fiable": "Fuente desactualizada o poco fiable: una de las fuentes reproduce un "
                          "dato viejo, de segunda mano o deliberadamente falso.",
    "ubicacion_imprecisa": "Ubicación declarada y no verificada, geolocalización imprecisa o "
                           "publicación diferida respecto del hecho.",
}
_READINGS_BY_KIND: dict[str, tuple[str, ...]] = {
    "espacio_temporal": ("suplantacion", "error_de_carga", "homonimos", "ubicacion_imprecisa",
                         "fuente_poco_fiable"),
    "valor_unico": ("suplantacion", "error_de_carga", "homonimos", "fuente_poco_fiable"),
    "intervalos_excluyentes": ("error_de_carga", "homonimos", "suplantacion",
                               "fuente_poco_fiable"),
}

# --- Utilidades ----------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia de círculo máximo entre dos puntos, en kilómetros."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


_PREDICATE_STOPWORDS = frozenset({"de", "del", "la", "el", "los", "las", "su", "nro", "numero"})
_PREDICATE_ALIASES = {"documento": "dni", "documento_identidad": "dni", "nacimiento": "fecha_nacimiento",
                      "nacio": "fecha_nacimiento", "fecha_nac": "fecha_nacimiento",
                      "fallecimiento": "fecha_fallecimiento", "ubicacion": "ubicado_en",
                      "libre": "en_libertad", "preso": "detenido"}


def normalize_predicate(predicate: str) -> str:
    """"Fecha de nacimiento" -> "fecha_nacimiento". Tolera cómo lo escriba la fuente o el modelo."""
    words = [w for w in re.split(r"[^a-z0-9]+", fold(predicate)) if w]
    kept = [w for w in words if w not in _PREDICATE_STOPWORDS] or words
    name = "_".join(kept)
    return _PREDICATE_ALIASES.get(name, name)


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


_FAR_FUTURE = datetime(9999, 1, 1, tzinfo=UTC)


def _interval(claim: Claim) -> tuple[datetime, datetime] | None:
    if claim.start is None and claim.end is None:
        return None
    start = _utc(claim.start or claim.end)  # type: ignore[arg-type]
    if claim.open_end:
        end = _FAR_FUTURE
    else:
        end = _utc(claim.end) if claim.end is not None else start
    if end < start:
        start, end = end, start
    return start, end


def _is_window(claim: Claim) -> bool:
    """True si el intervalo es una ventana de incertidumbre y no una permanencia."""
    return claim.time_mode == "dentro_de" and claim.end is not None and not claim.open_end


def available_travel_seconds(a: Claim, b: Claim) -> float | None:
    """Mayor tiempo de traslado compatible con las dos afirmaciones (caso más favorable).

    Permanencias ("durante"): el hueco entre los intervalos, 0 si se pisan.
    Ventanas ("dentro_de"): el hecho pudo ocurrir en cualquier punto de la ventana, así que se
    toma la combinación de momentos que deja más tiempo.
    """
    ia, ib = _interval(a), _interval(b)
    if ia is None or ib is None:
        return None
    (a1, a2), (b1, b2) = ia, ib
    wa, wb = _is_window(a), _is_window(b)
    if wa and wb:
        best = max(b2 - a1, a2 - b1)
    elif wa:  # a es un momento dentro de [a1, a2]; b ocupa todo [b1, b2]
        best = max(b1 - a1, a2 - b2)
    elif wb:
        best = max(a1 - b1, b2 - a2)
    else:
        best = max(b1 - a2, a1 - b2)
    return max(0.0, best.total_seconds())


def _overlap_seconds(a: Claim, b: Claim) -> float | None:
    ia, ib = _interval(a), _interval(b)
    if ia is None or ib is None:
        return None
    start, end = max(ia[0], ib[0]), min(ia[1], ib[1])
    if end < start:
        return None
    return (end - start).total_seconds()


def _fmt_moment(claim: Claim) -> str:
    interval = _interval(claim)
    if interval is None:
        return "sin fecha"
    start, end = interval
    fmt = "%d/%m/%Y %H:%M UTC"
    if claim.open_end:
        return f"desde el {start.strftime(fmt)}"
    if end == start:
        return f"el {start.strftime(fmt)}"
    joiner = "en algún momento entre el" if _is_window(claim) else "entre el"
    return f"{joiner} {start.strftime(fmt)} y el {end.strftime(fmt)}"


def _fmt_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} segundos"
    if seconds < 2 * 3600:
        return f"{seconds / 60:.0f} minutos"
    if seconds < 72 * 3600:
        return f"{seconds / 3600:.1f} horas".replace(".", ",")
    return f"{seconds / 86400:.1f} días".replace(".", ",")


def _fmt_number(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _sources(claims: list[Claim]) -> list[str]:
    return list(dict.fromkeys(c.source or "(sin fuente)" for c in claims))


def _readings(kind: str) -> list[Reading]:
    return [Reading(kind=k, description=READINGS[k]) for k in _READINGS_BY_KIND[kind]]


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[-1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def normalize_value(predicate: str, value: str) -> str:
    """Forma comparable de un valor: "12.345.678" y "12345678" son el mismo DNI."""
    predicate = normalize_predicate(predicate)
    if predicate in {"dni", "cuit", "cuil", "cbu", "telefono"}:
        digits = re.sub(r"\D", "", value)
        return digits.lstrip("0") or fold(value)
    if predicate.startswith("fecha"):
        parsed = parse_local_date(value)
        if parsed is not None:
            return parsed.date().isoformat()
    return re.sub(r"[^\w ]", "", fold(value)).strip()


# --- Detección determinística --------------------------------------------------------------


def _detect_spacetime(claims: list[Claim], config: ContradictionConfig) -> list[Contradiction]:
    located = [c for c in claims
               if normalize_predicate(c.predicate) in config.presence_predicates
               and c.place is not None and c.place.has_coords and _interval(c) is not None]
    found = []
    for a, b in combinations(located, 2):
        assert a.place and b.place
        distance = haversine_km(a.place.lat, a.place.lon, b.place.lat, b.place.lon)  # type: ignore[arg-type]
        if distance <= config.min_distance_km:
            continue
        available = available_travel_seconds(a, b)
        if available is None:
            continue
        available += config.time_slack_minutes * 60
        needed = distance / config.max_speed_kmh * 3600
        if available >= needed:
            continue
        ratio = math.inf if available == 0 else needed / available
        if ratio >= 2 and distance >= 50:
            severity: Severity = "alta"
        elif ratio >= 1.2:
            severity = "media"
        else:
            severity = "baja"
        required_speed = None if available == 0 else distance / (available / 3600)
        name_a, name_b = a.place.name or "lugar A", b.place.name or "lugar B"
        if available == 0:
            timing = "al mismo tiempo"
            speed_text = "lo que no admite ningún traslado"
        else:
            timing = f"con {_fmt_duration(available)} de diferencia como máximo"
            speed_text = (f"lo que exigiría viajar a unos {_fmt_number(required_speed)} km/h "
                          f"(máximo considerado: {_fmt_number(config.max_speed_kmh)} km/h)")
        summary = (
            f"{a.subject} figura en {name_a} ({_fmt_moment(a)}, fuente {a.source or 's/d'}) y en "
            f"{name_b} ({_fmt_moment(b)}, fuente {b.source or 's/d'}), {timing}. Los separan "
            f"{_fmt_number(distance)} km, {speed_text}."
        )
        found.append(Contradiction(
            id="", kind="espacio_temporal", severity=severity, subject=a.subject,
            claim_ids=[a.id, b.id], sources=_sources([a, b]), summary=summary,
            details={"distancia_km": round(distance, 1),
                     "tiempo_disponible_s": round(available),
                     "tiempo_necesario_s": round(needed),
                     "velocidad_requerida_kmh": None if required_speed is None
                     else round(required_speed, 1),
                     "velocidad_maxima_kmh": config.max_speed_kmh,
                     "misma_fuente": a.source == b.source},
            readings=_readings("espacio_temporal")))
    return found


def _detect_single_valued(claims: list[Claim], config: ContradictionConfig) -> list[Contradiction]:
    by_predicate: dict[str, list[Claim]] = {}
    for claim in claims:
        predicate = normalize_predicate(claim.predicate)
        if predicate in config.single_valued_predicates and claim.value.strip():
            by_predicate.setdefault(predicate, []).append(claim)
    found = []
    for predicate, group in by_predicate.items():
        by_value: dict[str, list[Claim]] = {}
        for claim in group:
            by_value.setdefault(normalize_value(predicate, claim.value), []).append(claim)
        if len(by_value) < 2:
            continue
        sources = _sources(group)
        values = list(by_value)
        closest = min(_edit_distance(x, y) for x, y in combinations(values, 2))
        cross_source = len(sources) > 1
        strong = predicate in {"dni", "cuit", "cuil", "fecha_nacimiento"}
        severity: Severity = "alta" if strong and cross_source else "media" if cross_source else "baja"
        listing = "; ".join(
            f"«{members[0].value}» según {', '.join(_sources(members))}"
            for members in by_value.values()
        )
        summary = (f"{group[0].subject} tiene {len(by_value)} valores distintos para "
                   f"«{predicate}», que admite uno solo: {listing}.")
        if closest <= 2:
            summary += (f" Los valores difieren en {closest} carácter"
                        f"{'es' if closest != 1 else ''}, lo que es compatible con un error de "
                        "tipeo, sin que eso descarte las otras lecturas.")
        found.append(Contradiction(
            id="", kind="valor_unico", severity=severity, subject=group[0].subject,
            claim_ids=[c.id for c in group], sources=sources, summary=summary,
            details={"predicado": predicate,
                     "valores": {v: [c.id for c in members] for v, members in by_value.items()},
                     "distancia_edicion_minima": closest, "entre_fuentes": cross_source},
            readings=_readings("valor_unico")))
    return found


def _excluding(a: Claim, b: Claim, config: ContradictionConfig) -> str | None:
    pa, pb = normalize_predicate(a.predicate), normalize_predicate(b.predicate)
    if pa == pb:
        if pa in config.exclusive_value_predicates:
            va, vb = normalize_value(pa, a.value), normalize_value(pb, b.value)
            if va and vb and va != vb:
                return f"«{pa}» con valores distintos («{a.value}» y «{b.value}»)"
        return None
    if any(pa in group and pb in group for group in config.exclusive_groups):
        return f"«{pa}» y «{pb}»"
    return None


def _detect_exclusive_intervals(claims: list[Claim],
                                config: ContradictionConfig) -> list[Contradiction]:
    timed = [c for c in claims if _interval(c) is not None and not _is_window(c)]
    found = []
    for a, b in combinations(timed, 2):
        what = _excluding(a, b, config)
        if what is None:
            continue
        overlap = _overlap_seconds(a, b)
        if overlap is None:
            continue
        ia, ib = _interval(a), _interval(b)
        assert ia and ib
        if overlap == 0 and ia[0] != ia[1] and ib[0] != ib[1]:
            continue  # intervalos contiguos (uno termina cuando empieza el otro): no se pisan
        open_ended = a.open_end or b.open_end
        if overlap >= 86400 or (open_ended and overlap > 0):
            severity: Severity = "alta"
        elif overlap >= 3600 or overlap == 0:
            severity = "media"
        else:
            severity = "baja"
        if open_ended and overlap >= 86400 * 365 * 100:
            span = "sin fecha de fin conocida"
        elif overlap == 0:
            span = "en el mismo instante"
        else:
            span = f"durante {_fmt_duration(overlap)}"
        summary = (
            f"{a.subject} tiene estados que se excluyen, {what}, superpuestos {span}: "
            f"{_fmt_moment(a)} (fuente {a.source or 's/d'}) y {_fmt_moment(b)} "
            f"(fuente {b.source or 's/d'})."
        )
        found.append(Contradiction(
            id="", kind="intervalos_excluyentes", severity=severity, subject=a.subject,
            claim_ids=[a.id, b.id], sources=_sources([a, b]), summary=summary,
            details={"solapamiento_s": None if open_ended else round(overlap),
                     "intervalo_abierto": open_ended, "misma_fuente": a.source == b.source},
            readings=_readings("intervalos_excluyentes")))
    return found


def detect_contradictions(claims: list[Claim],
                          config: ContradictionConfig | None = None) -> list[Contradiction]:
    """Detección determinística. Mismo input, mismo output; no usa red ni LLM."""
    config = config or ContradictionConfig()
    by_subject: dict[str, list[Claim]] = {}
    for claim in claims:
        key = fold(claim.subject)
        if key:
            by_subject.setdefault(key, []).append(claim)
    found: list[Contradiction] = []
    for group in by_subject.values():
        found += _detect_spacetime(group, config)
        found += _detect_single_valued(group, config)
        found += _detect_exclusive_intervals(group, config)
    order = {"alta": 0, "media": 1, "baja": 2}
    found.sort(key=lambda c: (order[c.severity], c.kind, c.claim_ids))
    for number, contradiction in enumerate(found, start=1):
        contradiction.id = f"C{number}"
    return found


# --- LLM (1): texto -> afirmaciones --------------------------------------------------------


class _LLMClaim(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    sujeto: LooseStr = Field(validation_alias=AliasChoices("sujeto", "subject"))
    predicado: LooseStr = Field(validation_alias=AliasChoices("predicado", "predicate"))
    valor: LooseStr = Field("", validation_alias=AliasChoices("valor", "value"))
    desde: LooseStr = Field("", validation_alias=AliasChoices("desde", "momento", "fecha", "start"))
    hasta: LooseStr = Field("", validation_alias=AliasChoices("hasta", "end"))
    lugar: LooseStr = ""
    cita: LooseStr = Field(validation_alias=AliasChoices("cita", "texto", "quote"))
    confianza: Confidence = 0.6


_ClaimList = lenient_list(_LLMClaim)


class _LLMClaims(BaseModel):
    model_config = ConfigDict(extra="ignore")
    afirmaciones: _ClaimList = Field(default_factory=list)  # type: ignore[valid-type]


class ClaimExtraction(BaseModel):
    claims: list[Claim] = Field(default_factory=list)
    discarded: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


_RE_DATE_ONLY = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$|^\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\s*$")


def _parse_moment(value: str, tz: timezone) -> tuple[datetime | None, bool]:
    """(momento, es_solo_fecha)."""
    if not value or fold(value) in {"null", "none", "desconocido", "s/d"}:
        return None, False
    parsed = parse_local_date(value)
    if parsed is None:
        return None, False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    date_only = bool(_RE_DATE_ONLY.match(value)) or (
        parsed.hour == parsed.minute == parsed.second == 0 and ":" not in value)
    return parsed, date_only


async def extract_claims(
    text: str,
    source: str,
    client: FunesClient,
    *,
    gazetteer: dict[str, tuple[float, float]] | None = None,
    utc_offset_hours: float = -3.0,
    id_prefix: str | None = None,
    max_chars: int = 6000,
    overlap: int = 400,
) -> ClaimExtraction:
    """Convierte texto en afirmaciones estructuradas usando el LLM.

    Garantías determinísticas: la cita tiene que estar en el texto y el sujeto tiene que
    aparecer en él; si no, la afirmación se descarta. El LLM no aporta coordenadas: salen solo de
    `gazetteer` (nombre de lugar -> (lat, lon)). Las horas sin zona se interpretan con
    `utc_offset_hours` (por defecto, hora argentina).
    """
    result = ClaimExtraction()
    tz = timezone(timedelta(hours=utc_offset_hours))
    places = {fold(name): coords for name, coords in (gazetteer or {}).items()}
    prefix = id_prefix if id_prefix is not None else f"{source or 'fuente'}:"
    seen: set[tuple[str, str, str, int]] = set()
    chunks = split_text(text, max_chars=max_chars, overlap=overlap)
    for number, chunk in enumerate(chunks, start=1):
        document, nonce = prompts.wrap_untrusted(chunk.text)
        messages = [
            {"role": "system", "content": prompts.render(
                prompts.CLAIMS_SYSTEM, security=prompts.security_block("DOCUMENTO", nonce))},
            {"role": "user", "content": prompts.render(
                prompts.CLAIMS_USER, document=document, reminder=prompts.UNTRUSTED_REMINDER)},
        ]
        try:
            parsed = await client.chat_json(messages, _LLMClaims, wrap_list_as="afirmaciones")
        except (LLMError, StructuredOutputError) as exc:
            result.warnings.append(f"Fragmento {number}/{len(chunks)}: sin afirmaciones ({exc}).")
            continue
        for item in parsed.afirmaciones:
            raw = item.model_dump()
            match = locate_quote(chunk.text, item.cita, min_chars=4)
            if match is None:
                result.discarded.append({"reason": "la cita no aparece en el texto fuente", **raw})
                continue
            if not item.sujeto or locate_quote(text, item.sujeto) is None:
                result.discarded.append({"reason": "el sujeto no aparece en el texto fuente", **raw})
                continue
            predicate = normalize_predicate(item.predicado)
            if not predicate:
                result.discarded.append({"reason": "sin predicado", **raw})
                continue
            offset = chunk.start + match.start
            dedup = (fold(item.sujeto), predicate, fold(item.valor), offset)
            if dedup in seen:
                continue
            seen.add(dedup)
            start, start_date_only = _parse_moment(item.desde, tz)
            end, end_date_only = _parse_moment(item.hasta, tz)
            props: dict[str, Any] = {"metodo": "llm", "estado": "propuesta",
                                     "cita_exacta": match.exact}
            if item.desde and start is None:
                props["momento_no_interpretado"] = item.desde
            time_mode: Literal["durante", "dentro_de"] = "durante"
            if start is not None and end is None and start_date_only:
                # "el 10 de marzo" sin hora: ocurrió en algún momento de ese día.
                end = start + timedelta(days=1) - timedelta(seconds=1)
                time_mode = "dentro_de"
            elif end is not None and end_date_only:
                end = end + timedelta(days=1) - timedelta(seconds=1)
            place = None
            place_name = item.lugar or (item.valor if predicate in {"ubicado_en"} else "")
            if place_name and fold(place_name) not in {"null", "none"}:
                coords = places.get(fold(place_name))
                place = Place(name=place_name, lat=coords[0] if coords else None,
                              lon=coords[1] if coords else None)
            confidence = min(item.confianza, 0.9) * (1.0 if match.exact else 0.85)
            result.claims.append(Claim(
                id=f"{prefix}{len(result.claims) + 1}", subject=" ".join(item.sujeto.split()),
                predicate=predicate, value=item.valor, start=start, end=end, time_mode=time_mode,
                place=place, source=source, quote=match.text, offset=offset,
                confidence=round(confidence, 3), props=props))
    return result


# --- LLM (2): redacción de la explicación --------------------------------------------------


def _explain_data(contradiction: Contradiction, claims: dict[str, Claim]) -> str:
    lines = [f"Tipo: {contradiction.kind}", f"Severidad: {contradiction.severity}",
             f"Sujeto: {contradiction.subject}", f"Hecho detectado: {contradiction.summary}",
             "Afirmaciones en conflicto:"]
    for claim_id in contradiction.claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        place = f", lugar {claim.place.name}" if claim.place and claim.place.name else ""
        quote = f', cita "{claim.quote[:300]}"' if claim.quote else ""
        lines.append(f"- [{claim.id}] fuente {claim.source or 's/d'}: {claim.predicate} = "
                     f"{claim.value or '(sin valor)'}, {_fmt_moment(claim)}{place}{quote}")
    lines.append("Lecturas posibles (ninguna está confirmada):")
    lines += [f"- {reading.description}" for reading in contradiction.readings]
    return "\n".join(lines)


async def explain_contradictions(
    contradictions: list[Contradiction],
    claims: list[Claim],
    client: FunesClient | None,
    *,
    max_chars: int = 900,
) -> list[Contradiction]:
    """Completa `explanation` de cada contradicción. Nunca modifica lo detectado.

    Si no hay cliente, el LLM falla o su texto usa lenguaje categórico, queda la redacción
    determinística (`summary`) y `explanation_source == "plantilla"`.
    """
    by_id = {claim.id: claim for claim in claims}
    out = []
    for contradiction in contradictions:
        text, origin = contradiction.summary, "plantilla"
        if client is not None:
            data, nonce = prompts.wrap_untrusted(_explain_data(contradiction, by_id), "DATOS")
            messages = [
                {"role": "system", "content": prompts.render(
                    prompts.CONTRADICTION_EXPLAIN_SYSTEM,
                    security=prompts.security_block("DATOS", nonce))},
                {"role": "user", "content": prompts.render(
                    prompts.CONTRADICTION_EXPLAIN_USER, data=data)},
            ]
            try:
                answer = " ".join((await client.chat(messages, temperature=0.2)).split())
            except LLMError:
                answer = ""
            if answer and len(answer) <= max_chars and not find_categorical(answer):
                text, origin = answer, "llm"
        out.append(contradiction.model_copy(
            update={"explanation": text, "explanation_source": origin}))
    return out
