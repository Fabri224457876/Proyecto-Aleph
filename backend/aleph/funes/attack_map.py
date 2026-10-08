"""Mapeo asistido de comportamiento a técnicas MITRE ATT&CK.

La lista de técnicas válidas llega por parámetro (la provee `cti/`). Garantías determinísticas:
todo id que devuelva el modelo y no esté en esa lista se descarta, el nombre de la técnica sale
de la lista (no del modelo) y la cita tiene que estar en el texto. Son candidatos para revisión.
"""

import re
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from aleph.funes import prompts
from aleph.funes.citations import locate_quote
from aleph.funes.client import FunesClient, LLMError, split_text
from aleph.funes.embed import embed_texts
from aleph.funes.structured import Confidence, LooseStr, StructuredOutputError, lenient_list

_RE_TECHNIQUE_ID = re.compile(r"T\d{4}(?:\.\d{3})?", re.I)


class Technique(BaseModel):
    id: str  # "T1566" o "T1566.001"
    name: str
    description: str = ""  # opcional; mejora la preselección por embeddings


class TechniqueEvidence(BaseModel):
    cita: str
    offset: int
    motivo: str = ""


class TechniqueCandidate(BaseModel):
    id: str
    name: str
    confidence: float
    evidence: list[TechniqueEvidence]
    estado: str = "propuesta"


class AttackMapping(BaseModel):
    candidates: list[TechniqueCandidate] = Field(default_factory=list)
    discarded: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class _LLMTechnique(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    id: LooseStr = Field(validation_alias=AliasChoices("id", "tecnica", "technique_id"))
    cita: LooseStr = Field("", validation_alias=AliasChoices("cita", "texto", "quote"))
    motivo: LooseStr = Field("", validation_alias=AliasChoices("motivo", "justificacion", "razon"))
    confianza: Confidence = 0.5


_TechniqueList = lenient_list(_LLMTechnique)


class _LLMMapping(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tecnicas: _TechniqueList = Field(default_factory=list)  # type: ignore[valid-type]


def _canonical_id(raw: str) -> str:
    match = _RE_TECHNIQUE_ID.search(raw)
    return match.group(0).upper() if match else raw.strip().upper()


async def _shortlist(text_chunks: list[str], techniques: list[Technique], client: FunesClient,
                     size: int) -> list[Technique]:
    """Preselección por similitud de embeddings entre el texto y las técnicas."""
    described = [f"{t.id} {t.name}. {t.description}".strip() for t in techniques]
    technique_vectors = await embed_texts(client, described)
    chunk_vectors = await embed_texts(client, text_chunks)
    if not technique_vectors.shape[1] or not chunk_vectors.shape[1]:
        raise LLMError("embeddings vacíos")
    best = (technique_vectors @ chunk_vectors.T).max(axis=1)
    keep = sorted(sorted(range(len(techniques)), key=lambda i: -best[i])[:size])
    return [techniques[i] for i in keep]


async def map_techniques(
    text: str,
    techniques: list[Technique],
    client: FunesClient,
    *,
    max_in_prompt: int = 200,
    max_chars: int = 5000,
    overlap: int = 300,
    use_embeddings: bool = True,
) -> AttackMapping:
    """Propone técnicas ATT&CK candidatas para el comportamiento descripto en `text`.

    Si la lista de técnicas no entra en un prompt (`max_in_prompt`), se preselecciona por
    embeddings; si eso no está disponible, se recorre la lista por tandas.
    """
    result = AttackMapping()
    valid: dict[str, Technique] = {}
    for technique in techniques:
        valid.setdefault(_canonical_id(technique.id), technique)
    if not valid or not text.strip():
        return result
    chunks = split_text(text, max_chars=max_chars, overlap=overlap)
    catalog = list(valid.values())
    batches = [catalog]
    if len(catalog) > max_in_prompt:
        shortlisted = None
        if use_embeddings and client.embed_base_url:
            try:
                shortlisted = await _shortlist([c.text for c in chunks], catalog, client,
                                               max_in_prompt)
            except LLMError as exc:
                result.warnings.append(f"Preselección por embeddings no disponible ({exc}); "
                                       "se recorre la lista completa por tandas.")
        batches = [shortlisted] if shortlisted else [
            catalog[i:i + max_in_prompt] for i in range(0, len(catalog), max_in_prompt)]

    found: dict[str, TechniqueCandidate] = {}
    for chunk in chunks:
        document, nonce = prompts.wrap_untrusted(chunk.text)
        for batch in batches:
            listing = "\n".join(f"{_canonical_id(t.id)} — {t.name}" for t in batch)
            messages = [
                {"role": "system", "content": prompts.render(
                    prompts.ATTACK_SYSTEM, techniques=listing,
                    security=prompts.security_block("DOCUMENTO", nonce))},
                {"role": "user", "content": prompts.render(
                    prompts.ATTACK_USER, document=document,
                    reminder=prompts.UNTRUSTED_REMINDER)},
            ]
            try:
                parsed = await client.chat_json(messages, _LLMMapping, wrap_list_as="tecnicas")
            except (LLMError, StructuredOutputError) as exc:
                result.warnings.append(f"Sin respuesta utilizable del modelo ({exc}).")
                continue
            offered = {_canonical_id(t.id) for t in batch}
            for item in parsed.tecnicas:
                technique_id = _canonical_id(item.id)
                raw = item.model_dump()
                # Solo valen ids de la lista válida y, además, de la tanda que vio el modelo.
                if technique_id not in valid or technique_id not in offered:
                    result.discarded.append({"reason": "id fuera de la lista de técnicas válidas",
                                             **raw})
                    continue
                match = locate_quote(chunk.text, item.cita, min_chars=6)
                if match is None:
                    result.discarded.append({"reason": "la cita no aparece en el texto fuente",
                                             **raw})
                    continue
                confidence = round(min(item.confianza, 0.9) * (1.0 if match.exact else 0.85), 3)
                evidence = TechniqueEvidence(cita=match.text, offset=chunk.start + match.start,
                                             motivo=item.motivo[:400])
                candidate = found.get(technique_id)
                if candidate is None:
                    found[technique_id] = TechniqueCandidate(
                        id=technique_id, name=valid[technique_id].name, confidence=confidence,
                        evidence=[evidence])
                else:
                    candidate.confidence = max(candidate.confidence, confidence)
                    if all(e.offset != evidence.offset for e in candidate.evidence):
                        candidate.evidence.append(evidence)
    result.candidates = sorted(found.values(), key=lambda c: (-c.confidence, c.id))
    return result
