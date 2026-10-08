"""Extracción de entidades, híbrida: reglas determinísticas primero, LLM después.

- Reglas (`ar_rules`): DNI, CUIT/CUIL, CBU, patentes, teléfonos, emails, URLs, fechas, direcciones.
- LLM: personas, organizaciones, lugares, alias, eventos y las relaciones entre entidades.

Defensa contra alucinaciones: cada entidad o relación del LLM trae una cita; si la cita no está
en el texto fuente se descarta. La etiqueta de la entidad sale del texto fuente, no de lo que
escribió el modelo. Todo lo devuelto es una propuesta (`props["estado"] == "propuesta"`) que
confirma un analista.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from aleph.core.schemas import EntityRecord, RelationRecord
from aleph.funes import prompts
from aleph.funes.ar_rules import DateHit, RuleHit, find_rule_hits
from aleph.funes.citations import fold, locate_quote
from aleph.funes.client import FunesClient, LLMError, TextChunk, split_text
from aleph.funes.structured import Confidence, LooseStr, StructuredOutputError, lenient_list

# --- Vocabulario ---------------------------------------------------------------------------

_ENTITY_TYPES = {
    "persona": "person", "person": "person", "per": "person", "individuo": "person",
    "organizacion": "organization", "organization": "organization", "org": "organization",
    "empresa": "organization", "institucion": "organization", "organismo": "organization",
    "lugar": "location", "location": "location", "loc": "location", "ubicacion": "location",
    "localidad": "location",
    "evento": "event", "event": "event", "hecho": "event",
    "alias": "alias", "apodo": "alias", "sobrenombre": "alias", "nick": "alias",
}
# Tipo en español (el que ve el modelo) -> tipo de relación del grafo.
RELATION_TYPES = {
    "miembro_de": "member_of", "trabaja_para": "works_for", "familiar_de": "family_of",
    "vinculado_con": "associated_with", "ubicado_en": "located_in",
    "participo_en": "participated_in", "propietario_de": "owns", "alias_de": "alias_of",
    "se_comunico_con": "communicated_with", "identificado_por": "identified_by", "usa": "uses",
}
FALLBACK_RELATION = "related_to"
MAX_LLM_CONFIDENCE = 0.9  # lo que propone un LLM nunca vale como certeza
_MAX_QUOTE = {"event": 240}
_DEFAULT_MAX_QUOTE = 120
_RULE_NAMES_ES = {"dni": "DNI", "cuit": "CUIT/CUIL", "cbu": "CBU", "patente": "patente",
                  "telefono": "teléfono", "email": "email", "url": "URL", "direccion": "dirección"}

# --- Esquema de la respuesta del LLM (tolerante) -------------------------------------------


class _LLMEntity(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: LooseStr = Field("", validation_alias=AliasChoices("id", "ref"))
    tipo: LooseStr = Field(validation_alias=AliasChoices("tipo", "type", "clase"))
    texto: LooseStr = Field(validation_alias=AliasChoices("texto", "cita", "text", "mencion"))
    nombre: LooseStr = Field("", validation_alias=AliasChoices("nombre", "name", "label"))
    confianza: Confidence = 0.6


class _LLMRelation(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    origen: LooseStr = Field(validation_alias=AliasChoices("origen", "src", "source", "desde"))
    destino: LooseStr = Field(validation_alias=AliasChoices("destino", "dst", "target", "hacia"))
    tipo: LooseStr = Field("", validation_alias=AliasChoices("tipo", "type", "relacion"))
    cita: LooseStr = Field("", validation_alias=AliasChoices("cita", "texto", "quote"))
    confianza: Confidence = 0.6


_EntityList = lenient_list(_LLMEntity)
_RelationList = lenient_list(_LLMRelation)


class _LLMExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entidades: _EntityList = Field(default_factory=list)  # type: ignore[valid-type]
    relaciones: _RelationList = Field(default_factory=list)  # type: ignore[valid-type]


# --- Resultado -----------------------------------------------------------------------------


class DateMention(BaseModel):
    """Fecha detectada por reglas. No es una entidad del grafo; sirve a la línea de tiempo."""

    iso: str
    cita: str
    offset: int
    con_hora: bool = False
    anio_de_dos_digitos: bool = False


class Discarded(BaseModel):
    """Algo que propuso el LLM y se descartó, con el motivo. Para auditoría y evaluación."""

    kind: str  # "entidad" | "relacion"
    reason: str
    data: dict[str, Any] = Field(default_factory=dict)


class NerResult(BaseModel):
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    dates: list[DateMention] = Field(default_factory=list)
    discarded: list[Discarded] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass
class _Mention:
    type: str
    label: str
    key: str  # clave de deduplicación dentro del tipo
    quote: str
    offset: int
    method: str  # "regla" | "llm"
    confidence: float
    props: dict[str, Any] = field(default_factory=dict)
    local_id: str = ""  # id con el que el LLM puede referirla (por fragmento)
    tokens: tuple[str, ...] = ()  # nombres completos plegados (personas)
    initials: tuple[str, ...] = ()


# --- Normalización de nombres --------------------------------------------------------------

_TITLES = frozenset(
    "sr sra srta sres dr dra dres lic ing cdor cdra don dona arq prof cnel gral tte cabo sgto "
    "comisario fiscal juez jueza diputado diputada senador senadora ab abog".split()
)
_PARTICLES = frozenset({"de", "del", "la", "las", "los", "y", "da", "di", "van", "von"})
_RE_NAME_TOKEN = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*\.?")


def _name_parts(name: str) -> list[str]:
    """Tokens del nombre en orden natural, sin títulos. "PEREZ, Juan" -> ["Juan", "PEREZ"]."""
    name = " ".join(name.split())
    if name.count(",") == 1:
        last, first = (part.strip() for part in name.split(","))
        if first and last:
            name = f"{first} {last}"
    parts = _RE_NAME_TOKEN.findall(name)
    return [p for p in parts if fold(p.rstrip(".")) not in _TITLES or len(parts) == 1]


def person_signature(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(nombres completos plegados, iniciales) para comparar variantes de un mismo nombre."""
    full: list[str] = []
    initials: list[str] = []
    for part in _name_parts(name):
        folded = fold(part.rstrip("."))
        if not folded or folded in _PARTICLES:
            continue
        if len(folded) == 1:
            initials.append(folded)
        else:
            full.append(folded)
    return tuple(full), tuple(initials)


def normalize_person_name(name: str) -> str:
    """Forma para mostrar: orden natural, sin título, sin MAYÚSCULAS sostenidas."""
    out = []
    for part in _name_parts(name):
        bare = part.rstrip(".")
        if fold(bare) in _PARTICLES and out:
            out.append(bare.lower())
        elif len(bare) == 1:
            out.append(bare.upper() + ".")
        elif bare.isupper() or bare.islower():
            out.append("-".join(piece.capitalize() for piece in bare.split("-")))
        else:
            out.append(bare)
    return " ".join(out) or " ".join(name.split())


def _clean_label(text: str) -> str:
    return " ".join(text.split()).strip(" ,;:.\"'“”«»")


def _generic_key(label: str) -> str:
    return re.sub(r"[^\w ]", "", fold(label)).strip()


def _supported_by(name: str, quote: str) -> bool:
    """¿Todas las palabras de `name` están en la cita? Evita etiquetas que el texto no dice."""
    name_tokens = set(re.findall(r"\w+", fold(name)))
    return bool(name_tokens) and name_tokens <= set(re.findall(r"\w+", fold(quote)))


# --- Reglas -> menciones -------------------------------------------------------------------


def _rule_mention(hit: RuleHit, index: int) -> _Mention:
    # Las direcciones comparten clave con los lugares del LLM para fusionarse si coinciden.
    key = _generic_key(hit.label) if hit.rule == "direccion" else hit.value
    return _Mention(hit.type, hit.label, key, hit.quote, hit.start, "regla",
                    hit.confidence, {"regla": hit.rule, **hit.props}, local_id=f"r{index}")


def _date_mention(hit: DateHit) -> DateMention:
    return DateMention(iso=hit.iso, cita=hit.quote, offset=hit.start, con_hora=hit.has_time,
                       anio_de_dos_digitos=hit.two_digit_year)


# --- LLM -> menciones ----------------------------------------------------------------------


def _build_messages(chunk: TextChunk, rule_mentions: list[_Mention]) -> list[dict[str, str]]:
    document, nonce = prompts.wrap_untrusted(chunk.text)
    if rule_mentions:
        lines = "\n".join(
            f'- {m.local_id}: {_RULE_NAMES_ES.get(m.props.get("regla", ""), m.type)} "{m.quote}"'
            for m in rule_mentions
        )
        rules_block = prompts.render(prompts.NER_RULE_ENTITIES, lines=lines)
    else:
        rules_block = prompts.NER_NO_RULE_ENTITIES
    system = prompts.render(prompts.NER_SYSTEM, security=prompts.security_block("DOCUMENTO", nonce))
    user = prompts.render(prompts.NER_USER, rule_entities=rules_block, document=document,
                          reminder=prompts.UNTRUSTED_REMINDER)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _entity_from_llm(item: _LLMEntity, chunk: TextChunk) -> _Mention | str:
    """Devuelve la mención validada o el motivo de descarte."""
    kind = _ENTITY_TYPES.get(fold(item.tipo))
    if kind is None:
        return f"tipo desconocido: {item.tipo!r}"
    match = locate_quote(chunk.text, item.texto)
    if match is None and item.nombre:
        match = locate_quote(chunk.text, item.nombre)
    if match is None:
        return "la cita no aparece en el texto fuente"
    start, quote, exact = match.start, match.text, match.exact
    # Si citó una oración entera pero el nombre está adentro, la mención es el nombre.
    if item.nombre and _supported_by(item.nombre, quote):
        inner = locate_quote(quote, item.nombre)
        if inner is not None:
            start, quote, exact = start + inner.start, inner.text, exact and inner.exact
    entity_type = "person" if kind == "alias" else kind
    if len(quote) > _MAX_QUOTE.get(entity_type, _DEFAULT_MAX_QUOTE):
        return "la cita es demasiado larga para ser una mención"
    confidence = min(item.confianza, MAX_LLM_CONFIDENCE) * (1.0 if exact else 0.85)
    props: dict[str, Any] = {"cita_exacta": exact}
    tokens: tuple[str, ...] = ()
    initials: tuple[str, ...] = ()
    if kind == "person":
        source = item.nombre if item.nombre and _supported_by(item.nombre, quote) else quote
        label = normalize_person_name(source)
        tokens, initials = person_signature(source)
        if not tokens and not initials:
            return "la mención no contiene un nombre"
        key = ""
    else:
        label = _clean_label(quote)
        if not label:
            return "la mención quedó vacía"
        key = _generic_key(label)
        if kind == "alias":
            props["kind"] = "alias"
            key = f"alias:{key}"
    return _Mention(entity_type, label, key, quote, chunk.start + start, "llm",
                    round(confidence, 3), props, local_id=item.id, tokens=tokens,
                    initials=initials)


@dataclass
class _PendingRelation:
    src: _Mention
    dst: _Mention
    type: str
    props: dict[str, Any]
    confidence: float


def _mentions_endpoint(quote: str, mention: _Mention) -> bool:
    folded = fold(quote)
    if fold(mention.quote) in folded or fold(mention.label) in folded:
        return True
    return any(len(tok) >= 4 and tok in folded for tok in re.findall(r"\w+", fold(mention.label)))


def _relation_from_llm(item: _LLMRelation, chunk: TextChunk,
                       by_id: dict[str, _Mention]) -> _PendingRelation | str:
    src, dst = by_id.get(item.origen), by_id.get(item.destino)
    if src is None or dst is None:
        return "referencia a una entidad inexistente o descartada"
    if src is dst:
        return "relación de una entidad consigo misma"
    match = locate_quote(chunk.text, item.cita, min_chars=4)
    if match is None:
        return "la cita no aparece en el texto fuente"
    raw_type = re.sub(r"\s+", "_", fold(item.tipo))
    props: dict[str, Any] = {"cita": match.text, "offset": chunk.start + match.start,
                             "metodo": "llm", "estado": "propuesta", "cita_exacta": match.exact}
    rel_type = RELATION_TYPES.get(raw_type)
    if rel_type is None:
        rel_type = raw_type if raw_type in RELATION_TYPES.values() else FALLBACK_RELATION
        if rel_type == FALLBACK_RELATION and item.tipo:
            props["tipo_original"] = item.tipo
    confidence = min(item.confianza, MAX_LLM_CONFIDENCE) * (1.0 if match.exact else 0.85)
    both = _mentions_endpoint(match.text, src) and _mentions_endpoint(match.text, dst)
    props["extremos_en_cita"] = both
    if not both:  # la cita existe pero no nombra a las dos partes: respaldo débil
        confidence *= 0.6
    return _PendingRelation(src, dst, rel_type, props, round(confidence, 3))


# --- Deduplicación -------------------------------------------------------------------------


def _assign_person_keys(mentions: list[_Mention]) -> None:
    """Agrupa variantes de un nombre: "Juan Pérez", "PEREZ, Juan" y, si no es ambiguo, "J. Pérez"."""
    people = [m for m in mentions if m.type == "person" and not m.key]
    full_names: dict[str, set[str]] = {}
    for m in people:
        if not m.initials and m.tokens:
            m.key = "p:" + " ".join(sorted(m.tokens))
            full_names[m.key] = set(m.tokens)
    for m in people:
        if m.key:
            continue
        candidates = []
        for key, tokens in full_names.items():
            if not m.tokens or not set(m.tokens) <= tokens:
                continue
            remaining = sorted(tokens - set(m.tokens))
            needed = list(m.initials)
            for token in remaining:
                if token[0] in needed:
                    needed.remove(token[0])
            if not needed:
                candidates.append(key)
        if len(candidates) == 1:  # con dos candidatos ("Juan Pérez" y "José Pérez") no se fusiona
            m.key = candidates[0]
        else:
            m.key = "p:" + " ".join(sorted(m.tokens)) + "|" + " ".join(sorted(m.initials))


def _best_label(group: list[_Mention]) -> str:
    if group[0].type != "person":
        return group[0].label
    return max(group, key=lambda m: (not m.initials, len(m.tokens), -m.offset)).label


def _merge(mentions: list[_Mention]) -> tuple[list[EntityRecord], dict[int, str]]:
    _assign_person_keys(mentions)
    groups: dict[tuple[str, str], list[_Mention]] = {}
    for m in sorted(mentions, key=lambda m: (m.offset, m.method != "regla")):
        groups.setdefault((m.type, m.key), []).append(m)
    entities: list[EntityRecord] = []
    ref_of: dict[int, str] = {}
    for number, group in enumerate(groups.values(), start=1):
        ref = f"e{number}"
        first = group[0]
        props: dict[str, Any] = {**first.props, "cita": first.quote, "offset": first.offset,
                                 "metodo": first.method, "estado": "propuesta"}
        seen: set[tuple[int, str]] = set()
        occurrences = []
        for m in group:
            ref_of[id(m)] = ref
            if (m.offset, m.quote) not in seen:
                seen.add((m.offset, m.quote))
                occurrences.append({"cita": m.quote, "offset": m.offset, "metodo": m.method})
        if len(occurrences) > 1:
            props["menciones"] = occurrences
        variants = list(dict.fromkeys(_clean_label(m.quote) for m in group))
        if len(variants) > 1:
            props["variantes"] = variants
        methods = sorted({m.method for m in group})
        if len(methods) > 1:
            props["metodos"] = methods
        entities.append(EntityRecord(type=first.type, label=_best_label(group), props=props,
                                     confidence=max(m.confidence for m in group), ref=ref))
    return entities, ref_of


# --- API pública ---------------------------------------------------------------------------


def extract_rule_entities(text: str) -> tuple[list[EntityRecord], list[DateMention]]:
    """Solo la parte determinística. No usa red."""
    hits, dates = find_rule_hits(text)
    mentions = [_rule_mention(hit, i) for i, hit in enumerate(hits, start=1)]
    entities, _ = _merge(mentions)
    return entities, [_date_mention(d) for d in dates]


async def extract_entities(
    text: str,
    client: FunesClient | None = None,
    *,
    use_llm: bool = True,
    max_chars: int = 6000,
    overlap: int = 400,
) -> NerResult:
    """Extrae entidades y relaciones de `text`.

    Sin `client` (o con `use_llm=False`) devuelve solo lo que encuentran las reglas. Si el LLM
    falla en un fragmento, se registra un aviso y se sigue con el resto: las reglas no dependen
    de él.
    """
    result = NerResult()
    hits, dates = find_rule_hits(text)
    result.dates = [_date_mention(d) for d in dates]
    rule_mentions = [_rule_mention(hit, i) for i, hit in enumerate(hits, start=1)]
    mentions: list[_Mention] = list(rule_mentions)
    pending: list[_PendingRelation] = []

    if use_llm and client is not None and text.strip():
        chunks = split_text(text, max_chars=max_chars, overlap=overlap)
        for number, chunk in enumerate(chunks, start=1):
            in_chunk = [m for m in rule_mentions
                        if chunk.start <= m.offset and m.offset + len(m.quote) <= chunk.end]
            try:
                extraction = await client.chat_json(
                    _build_messages(chunk, in_chunk), _LLMExtraction, wrap_list_as="entidades"
                )
            except (LLMError, StructuredOutputError) as exc:
                result.warnings.append(
                    f"Fragmento {number}/{len(chunks)}: el LLM no devolvió una extracción "
                    f"utilizable ({exc}). Se conservan las entidades detectadas por reglas."
                )
                continue
            by_id: dict[str, _Mention] = {m.local_id: m for m in in_chunk}
            for item in extraction.entidades:
                built = _entity_from_llm(item, chunk)
                if isinstance(built, str):
                    result.discarded.append(Discarded(
                        kind="entidad", reason=built,
                        data=item.model_dump(include={"tipo", "texto", "nombre"})))
                    continue
                mentions.append(built)
                # Un id repetido o que pisa uno de reglas no se puede referir sin ambigüedad.
                if built.local_id and built.local_id not in by_id:
                    by_id[built.local_id] = built
            for item in extraction.relaciones:
                relation = _relation_from_llm(item, chunk, by_id)
                if isinstance(relation, str):
                    result.discarded.append(Discarded(
                        kind="relacion", reason=relation,
                        data=item.model_dump(include={"origen", "destino", "tipo", "cita"})))
                    continue
                pending.append(relation)

    result.entities, ref_of = _merge(mentions)
    merged: dict[tuple[str, str, str], RelationRecord] = {}
    for relation in pending:
        src, dst = ref_of[id(relation.src)], ref_of[id(relation.dst)]
        if src == dst:
            continue
        key = (src, dst, relation.type)
        if key not in merged or relation.confidence > merged[key].confidence:
            merged[key] = RelationRecord(src_ref=src, dst_ref=dst, type=relation.type,
                                         props=relation.props, confidence=relation.confidence)
    result.relations = list(merged.values())
    return result
