"""Resumen de caso y borrador de informe de inteligencia.

Regla dura: cada afirmación del informe referencia ids de los datos de entrada. El modelo
devuelve afirmaciones estructuradas con sus ids; acá se valida que existan (los que no, se marcan
como `[ID INEXISTENTE: …]`), se marca lo que queda sin respaldo y lo que usa lenguaje categórico,
y recién entonces se arma el Markdown con estructura fija. La sección de fuentes y el anexo de
referencias se generan sin LLM.
"""

import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from aleph.core.schemas import ClusterResult, EntityRecord, PairResult, RelationRecord
from aleph.funes import prompts
from aleph.funes.client import FunesClient, LLMError
from aleph.funes.contradictions import Contradiction
from aleph.funes.language import find_categorical
from aleph.funes.structured import LooseStr, StructuredOutputError, lenient_list

ConfidenceLevel = Literal["baja", "media", "alta"]
_LEVELS: dict[str, int] = {"baja": 0, "media": 1, "alta": 2}
_RE_ID = re.compile(r"\b([ERHGCF])-?(\d{1,5})\b")
_RE_BRACKETED = re.compile(r"\s*[\[(]\s*(?:[ERHGCF]-?\d{1,5}\s*[,;y ]*\s*)+[\])]")
_TYPE_NAMES_ES = {"person": "persona", "organization": "organización", "location": "lugar",
                  "event": "evento", "document": "documento", "vehicle": "vehículo",
                  "phone": "teléfono", "email": "email", "url": "URL", "account": "cuenta",
                  "domain": "dominio", "ip": "IP", "hash": "hash", "wallet": "billetera",
                  "malware": "malware", "vulnerability": "vulnerabilidad"}
DISCLAIMER = (
    "> Borrador generado por FUNES (IA local de Aleph) a partir de los datos del caso. "
    "Todo lo que sigue son hipótesis con evidencia para revisión de un analista: no afirma "
    "identidad ni responsabilidad de ninguna persona. Las marcas entre corchetes remiten a los "
    "datos del caso."
)

# --- Entrada -------------------------------------------------------------------------------


class SourceRef(BaseModel):
    id: str = ""
    title: str
    reference: str = ""  # URL, archivo o consulta de origen
    reliability: str = ""  # valoración de la fuente, si el caso la tiene


class CaseData(BaseModel):
    """Todo lo que el informe puede citar. Lo arma `api/` o `workers/` desde la base."""

    title: str = "Caso sin título"
    purpose: str = ""
    entities: list[EntityRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    pairs: list[PairResult] = Field(default_factory=list)  # hipótesis de mismo operador
    clusters: list[ClusterResult] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)


class InventoryItem(BaseModel):
    id: str  # id canónico del informe: E1, R1, H1, G1, C1, F1
    kind: Literal["entidad", "relacion", "hipotesis", "grupo", "contradiccion", "fuente"]
    text: str  # línea legible
    original: str = ""  # ref o id del dato de entrada
    confidence: ConfidenceLevel | None = None


def _clip(text: str, limit: int = 220) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _level(score: float) -> ConfidenceLevel:
    return "alta" if score >= 0.8 else "media" if score >= 0.5 else "baja"


def build_inventory(case: CaseData, *, max_entities: int = 150, max_relations: int = 200,
                    max_pairs: int = 60) -> tuple[list[InventoryItem], list[str]]:
    """Asigna ids canónicos a los datos del caso. Devuelve (inventario, avisos por recortes)."""
    items: list[InventoryItem] = []
    warnings: list[str] = []
    entity_ids: dict[str, str] = {}
    entities = sorted(enumerate(case.entities), key=lambda p: (-p[1].confidence, p[0]))
    if len(entities) > max_entities:
        warnings.append(f"Se incluyeron las {max_entities} entidades de mayor confianza de "
                        f"{len(entities)}.")
    for number, (_, entity) in enumerate(sorted(entities[:max_entities]), start=1):
        new_id = f"E{number}"
        if entity.ref:
            entity_ids[entity.ref] = new_id
        kind = _TYPE_NAMES_ES.get(entity.type, entity.type)
        if entity.props.get("kind"):
            kind += f" ({entity.props['kind']})"
        items.append(InventoryItem(
            id=new_id, kind="entidad", original=entity.ref, confidence=_level(entity.confidence),
            text=f"{kind}: {_clip(entity.label, 120)} (confianza {entity.confidence:.2f})"))
    usable = [r for r in case.relations if r.src_ref in entity_ids and r.dst_ref in entity_ids]
    if len(usable) < len(case.relations):
        warnings.append(f"{len(case.relations) - len(usable)} relaciones quedaron fuera porque "
                        "refieren entidades que no están en el inventario.")
    if len(usable) > max_relations:
        warnings.append(f"Se incluyeron {max_relations} relaciones de {len(usable)}.")
        usable = sorted(usable, key=lambda r: -r.confidence)[:max_relations]
    for number, relation in enumerate(usable, start=1):
        items.append(InventoryItem(
            id=f"R{number}", kind="relacion", confidence=_level(relation.confidence),
            original=f"{relation.src_ref}->{relation.dst_ref}:{relation.type}",
            text=f"{entity_ids[relation.src_ref]} --{relation.type}--> "
                 f"{entity_ids[relation.dst_ref]} (confianza {relation.confidence:.2f})"))
    pairs = sorted(case.pairs, key=lambda p: -p.score)
    if len(pairs) > max_pairs:
        warnings.append(f"Se incluyeron las {max_pairs} hipótesis de mayor puntaje de {len(pairs)}.")
    for number, pair in enumerate(pairs[:max_pairs], start=1):
        text = (f"hipótesis de mismo operador entre las cuentas {pair.a} y {pair.b}: puntaje "
                f"{pair.score:.2f}, confianza {pair.confidence}")
        if pair.summary:
            text += f". {_clip(pair.summary)}"
        items.append(InventoryItem(id=f"H{number}", kind="hipotesis", text=text,
                                   original=f"{pair.a}|{pair.b}", confidence=pair.confidence))
    for number, cluster in enumerate(case.clusters, start=1):
        text = (f"grupo de cuentas con hipótesis de mismo operador: {', '.join(cluster.members)} "
                f"(cohesión {cluster.cohesion:.2f})")
        if cluster.summary:
            text += f". {_clip(cluster.summary)}"
        items.append(InventoryItem(id=f"G{number}", kind="grupo", text=text,
                                   original="|".join(cluster.members),
                                   confidence=_level(cluster.cohesion)))
    for number, contradiction in enumerate(case.contradictions, start=1):
        readings = ", ".join(r.kind for r in contradiction.readings)
        items.append(InventoryItem(
            id=f"C{number}", kind="contradiccion", original=contradiction.id,
            text=f"contradicción {contradiction.kind}, severidad {contradiction.severity}: "
                 f"{_clip(contradiction.summary, 400)} Lecturas posibles: {readings}."))
    for number, source in enumerate(case.sources, start=1):
        text = f"fuente: {_clip(source.title, 120)}"
        if source.reference:
            text += f" — {_clip(source.reference, 160)}"
        if source.reliability:
            text += f" (valoración: {_clip(source.reliability, 60)})"
        items.append(InventoryItem(id=f"F{number}", kind="fuente", text=text, original=source.id))
    return items, warnings


# --- Respuesta del LLM (tolerante) ---------------------------------------------------------


def _ids_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int)):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, (str, int))]


class _LLMStatement(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    texto: LooseStr = Field(validation_alias=AliasChoices("texto", "text", "afirmacion"))
    ids: list[Any] | str | int | None = Field(
        None, validation_alias=AliasChoices("ids", "referencias", "refs", "id"))
    confianza: LooseStr = Field("", validation_alias=AliasChoices("confianza", "nivel"))


_Statements = lenient_list(_LLMStatement)


class _LLMReport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    resumen_ejecutivo: _Statements = Field(default_factory=list)  # type: ignore[valid-type]
    hallazgos: _Statements = Field(default_factory=list)  # type: ignore[valid-type]
    hipotesis: _Statements = Field(default_factory=list)  # type: ignore[valid-type]
    vacios: _Statements = Field(default_factory=list)  # type: ignore[valid-type]


class _LLMSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    resumen: _Statements = Field(default_factory=list)  # type: ignore[valid-type]


# --- Salida --------------------------------------------------------------------------------


class Statement(BaseModel):
    text: str
    ids: list[str] = Field(default_factory=list)  # ids citados que existen
    invalid_ids: list[str] = Field(default_factory=list)  # ids citados que no existen
    confidence: ConfidenceLevel | None = None  # solo en hipótesis
    unsupported: bool = False  # no cita ningún id válido
    categorical: list[str] = Field(default_factory=list)  # frases que afirman en vez de estimar
    notes: list[str] = Field(default_factory=list)


class ReportDraft(BaseModel):
    markdown: str
    sections: dict[str, list[Statement]] = Field(default_factory=dict)
    inventory: list[InventoryItem] = Field(default_factory=list)
    invalid_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_by: Literal["llm", "plantilla"] = "llm"


class CaseSummary(BaseModel):
    markdown: str
    statements: list[Statement] = Field(default_factory=list)
    inventory: list[InventoryItem] = Field(default_factory=list)
    invalid_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_by: Literal["llm", "plantilla"] = "llm"


# --- Validación determinística -------------------------------------------------------------


def _normalize_ids(raw: list[str]) -> list[str]:
    found: list[str] = []
    for value in raw:
        matches = _RE_ID.findall(str(value).upper())
        if matches:
            found += [f"{letter}{int(number)}" for letter, number in matches]
        elif str(value).strip():
            found.append(str(value).strip()[:40])  # algo que no parece un id: se marcará
    return list(dict.fromkeys(found))


def _coerce_level(value: str) -> ConfidenceLevel | None:
    text = value.strip().lower()
    for level in ("baja", "media", "alta"):
        if level in text or level[:-1] + "o" in text:
            return level  # type: ignore[return-value]
    return None


def validate_statement(item: _LLMStatement, inventory: dict[str, InventoryItem], *,
                       require_ids: bool, is_hypothesis: bool = False) -> Statement:
    """Separa ids válidos de inexistentes y marca lo que no se puede sostener."""
    # En el texto solo cuentan como ids los que van entre corchetes: "G20" o "F1" sueltos no.
    inline = [f"{letter}{int(number)}" for group in _RE_BRACKETED.finditer(item.texto)
              for letter, number in _RE_ID.findall(group.group(0))]
    cited = list(dict.fromkeys(_normalize_ids(_ids_list(item.ids)) + inline))
    text = " ".join(_RE_BRACKETED.sub("", item.texto).split())
    statement = Statement(
        text=text,
        ids=[i for i in cited if i in inventory],
        invalid_ids=[i for i in cited if i not in inventory],
        categorical=find_categorical(text),
    )
    statement.unsupported = require_ids and not statement.ids
    if is_hypothesis:
        level = _coerce_level(item.confianza) or "baja"
        # La confianza de una hipótesis no puede superar la de los datos que cita.
        caps = [inventory[i].confidence for i in statement.ids
                if inventory[i].kind in ("hipotesis", "grupo") and inventory[i].confidence]
        if caps:
            cap = min(caps, key=lambda c: _LEVELS[c])
            if _LEVELS[level] > _LEVELS[cap]:
                statement.notes.append(
                    f"confianza reducida de {level} a {cap} para no superar la de los datos citados")
                level = cap
        if statement.unsupported:
            level = "baja"
        statement.confidence = level
    return statement


def _render_statement(statement: Statement) -> str:
    prefix = f"**Confianza {statement.confidence}.** " if statement.confidence else ""
    marks = "".join(f"[{i}]" for i in statement.ids)
    marks += "".join(f"[ID INEXISTENTE: {i}]" for i in statement.invalid_ids)
    if statement.unsupported:
        marks += "[SIN RESPALDO EN LOS DATOS]"
    if statement.categorical:
        marks += "[REVISAR: lenguaje categórico]"
    return f"- {prefix}{statement.text} {marks}".rstrip()


def _render_section(title: str, statements: list[Statement], empty: str) -> list[str]:
    lines = [f"## {title}", ""]
    lines += [_render_statement(s) for s in statements] or [f"_{empty}_"]
    return [*lines, ""]


def _render_sources(case: CaseData, inventory: list[InventoryItem]) -> list[str]:
    lines = ["## 5. Fuentes", ""]
    sources = [item for item in inventory if item.kind == "fuente"]
    lines += [f"- [{item.id}] {item.text.removeprefix('fuente: ')}" for item in sources] or [
        "_El caso no tiene fuentes cargadas._"]
    return [*lines, ""]


def _render_references(sections: dict[str, list[Statement]],
                       inventory: dict[str, InventoryItem]) -> list[str]:
    cited = list(dict.fromkeys(i for statements in sections.values() for s in statements
                               for i in s.ids if inventory[i].kind != "fuente"))
    if not cited:
        return []
    order = {item_id: n for n, item_id in enumerate(inventory)}
    lines = ["## Anexo. Datos citados", ""]
    lines += [f"- [{i}] {inventory[i].text}" for i in sorted(cited, key=order.__getitem__)]
    return [*lines, ""]


def _render_validation(sections: dict[str, list[Statement]], warnings: list[str]) -> list[str]:
    statements = [s for group in sections.values() for s in group]
    notes = list(warnings)
    invalid = list(dict.fromkeys(i for s in statements for i in s.invalid_ids))
    if invalid:
        notes.append("El modelo citó ids que no existen en los datos del caso: "
                     + ", ".join(invalid) + ". Las afirmaciones que los usan están marcadas.")
    unsupported = sum(s.unsupported for s in statements)
    if unsupported:
        notes.append(f"{unsupported} afirmación(es) no citan ningún dato válido y están marcadas "
                     "como sin respaldo.")
    categorical = sum(bool(s.categorical) for s in statements)
    if categorical:
        notes.append(f"{categorical} afirmación(es) usan lenguaje categórico y hay que "
                     "reescribirlas en términos estimativos.")
    notes += [note for s in statements for note in s.notes]
    if not notes:
        return []
    return ["## Observaciones de validación automática", "", *[f"- {n}" for n in notes], ""]


def _markdown(case: CaseData, sections: dict[str, list[Statement]],
              inventory: list[InventoryItem], warnings: list[str]) -> str:
    by_id = {item.id: item for item in inventory}
    lines = [f"# Borrador de informe de inteligencia — {_clip(case.title, 150)}", "", DISCLAIMER, ""]
    if case.purpose:
        lines += [f"**Propósito del caso:** {_clip(case.purpose, 400)}", ""]
    lines += _render_section("1. Resumen ejecutivo", sections["resumen_ejecutivo"],
                             "Sin contenido.")
    lines += _render_section("2. Hallazgos", sections["hallazgos"], "Sin hallazgos.")
    lines += _render_section("3. Hipótesis y nivel de confianza", sections["hipotesis"],
                             "Sin hipótesis formuladas.")
    lines += _render_section("4. Vacíos de información", sections["vacios"],
                             "No se identificaron vacíos; revisar manualmente.")
    lines += _render_sources(case, inventory)
    lines += _render_references(sections, by_id)
    lines += _render_validation(sections, warnings)
    return "\n".join(lines).rstrip() + "\n"


# --- Redacción sin LLM ---------------------------------------------------------------------


def _fallback_sections(inventory: list[InventoryItem]) -> dict[str, list[Statement]]:
    """Informe mínimo armado solo con plantillas, para cuando el LLM no está o falla."""
    count = {kind: sum(item.kind == kind for item in inventory)
             for kind in ("entidad", "relacion", "hipotesis", "grupo", "contradiccion", "fuente")}
    first = {kind: [item.id for item in inventory if item.kind == kind][:5] for kind in count}
    summary = Statement(
        text=(f"El caso reúne {count['entidad']} entidades, {count['relacion']} relaciones, "
              f"{count['hipotesis']} hipótesis de mismo operador y {count['contradiccion']} "
              "contradicciones entre fuentes, todas pendientes de revisión."),
        ids=first["entidad"][:2] + first["hipotesis"][:1] + first["contradiccion"][:1])
    summary.unsupported = not summary.ids
    findings = [Statement(text=f"Se registró: {item.text}.", ids=[item.id])
                for item in inventory if item.kind in ("relacion", "contradiccion")][:25]
    hypotheses = [Statement(text=f"No se puede descartar la {item.text}.", ids=[item.id],
                            confidence=item.confidence or "baja")
                  for item in inventory if item.kind in ("hipotesis", "grupo")][:25]
    gaps = [Statement(text="El borrador se armó sin el modelo de lenguaje: falta la lectura "
                           "analítica de los datos.")]
    if not count["fuente"]:
        gaps.append(Statement(text="El caso no tiene fuentes cargadas y valoradas."))
    return {"resumen_ejecutivo": [summary], "hallazgos": findings, "hipotesis": hypotheses,
            "vacios": gaps}


def _messages(system_template: str, case: CaseData,
              inventory: list[InventoryItem]) -> list[dict[str, str]]:
    listing = "\n".join(f"[{item.id}] {item.text}" for item in inventory) or "(sin datos)"
    block, nonce = prompts.wrap_untrusted(listing, "DATOS")
    system = prompts.render(system_template, security=prompts.security_block("DATOS", nonce))
    user = prompts.render(prompts.REPORT_USER, title=_clip(case.title, 150),
                          purpose=_clip(case.purpose, 400) or "(no declarado)", inventory=block,
                          reminder=prompts.UNTRUSTED_REMINDER)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# --- API pública ---------------------------------------------------------------------------


async def draft_report(case: CaseData, client: FunesClient | None = None) -> ReportDraft:
    """Borrador de informe en Markdown con estructura fija y referencias validadas."""
    inventory, warnings = build_inventory(case)
    by_id = {item.id: item for item in inventory}
    generated_by: Literal["llm", "plantilla"] = "llm"
    sections: dict[str, list[Statement]] | None = None
    if client is not None and inventory:
        try:
            raw = await client.chat_json(_messages(prompts.REPORT_SYSTEM, case, inventory),
                                         _LLMReport, temperature=0.2)
        except (LLMError, StructuredOutputError) as exc:
            warnings.append(f"El modelo no devolvió un informe utilizable ({exc}); se usó la "
                            "plantilla determinística.")
        else:
            sections = {
                "resumen_ejecutivo": [validate_statement(s, by_id, require_ids=True)
                                      for s in raw.resumen_ejecutivo if s.texto],
                "hallazgos": [validate_statement(s, by_id, require_ids=True)
                              for s in raw.hallazgos if s.texto],
                "hipotesis": [validate_statement(s, by_id, require_ids=True, is_hypothesis=True)
                              for s in raw.hipotesis if s.texto],
                "vacios": [validate_statement(s, by_id, require_ids=False)
                           for s in raw.vacios if s.texto],
            }
            if not any(sections.values()):
                warnings.append("El modelo devolvió un informe vacío; se usó la plantilla "
                                "determinística.")
                sections = None
    if sections is None:
        sections = _fallback_sections(inventory)
        generated_by = "plantilla"
    invalid = list(dict.fromkeys(i for group in sections.values() for s in group
                                 for i in s.invalid_ids))
    return ReportDraft(markdown=_markdown(case, sections, inventory, warnings), sections=sections,
                       inventory=inventory, invalid_ids=invalid, warnings=warnings,
                       generated_by=generated_by)


async def summarize_case(case: CaseData, client: FunesClient | None = None) -> CaseSummary:
    """Resumen breve del caso, con las mismas reglas de respaldo y lenguaje que el informe."""
    inventory, warnings = build_inventory(case)
    by_id = {item.id: item for item in inventory}
    generated_by: Literal["llm", "plantilla"] = "llm"
    statements: list[Statement] = []
    if client is not None and inventory:
        try:
            raw = await client.chat_json(_messages(prompts.SUMMARY_SYSTEM, case, inventory),
                                         _LLMSummary, wrap_list_as="resumen", temperature=0.2)
            statements = [validate_statement(s, by_id, require_ids=True)
                          for s in raw.resumen if s.texto]
        except (LLMError, StructuredOutputError) as exc:
            warnings.append(f"El modelo no devolvió un resumen utilizable ({exc}); se usó la "
                            "plantilla determinística.")
    if not statements:
        statements = _fallback_sections(inventory)["resumen_ejecutivo"]
        generated_by = "plantilla"
    lines = [f"**Resumen del caso — {_clip(case.title, 150)}**", ""]
    lines += [_render_statement(s) for s in statements]
    invalid = list(dict.fromkeys(i for s in statements for i in s.invalid_ids))
    return CaseSummary(markdown="\n".join(lines) + "\n", statements=statements,
                       inventory=inventory, invalid_ids=invalid, warnings=warnings,
                       generated_by=generated_by)
