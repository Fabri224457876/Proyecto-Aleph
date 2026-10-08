"""Integración de FUNES: NER, contradicciones y borrador de informe, con el LLM local del cluster.

Reglas:
- Todo lo que propone el LLM llega como propuesta y pasa por la validación determinística del motor.
- NER: sin LLM (o con el LLM caído) entrega lo que sale por reglas y lo dice en `llm_status`.
- Contradicciones con fuentes de texto y borrador de informe: si el LLM no está configurado o no
  responde, el pedido falla con 503 y un mensaje claro. La detección de contradicciones sobre
  afirmaciones ya estructuradas no necesita LLM, así que no se exige; la explicación usa plantilla
  si el LLM no está.
- Los timeouts son cortos para conectar (5 s): un LLM caído no tiene que colgar la API.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from aleph.core.models import AccountLink, Entity, Relation, Source
from aleph.core.schemas import EntityRecord, PairResult, RelationRecord
from aleph.funes.client import FunesClient, LLMError, NonLocalEndpointError
from aleph.funes.contradictions import (
    Claim,
    Contradiction,
    ContradictionConfig,
    detect_contradictions,
    explain_contradictions,
    extract_claims,
)
from aleph.funes.ner import NerResult, extract_entities
from aleph.funes.report import CaseData, ReportDraft, SourceRef, draft_report
from aleph.funes.structured import StructuredOutputError

from ..errors import Conflict, EngineUnavailable, Invalid
from .proposals import make_source

LLM_TIMEOUT = httpx.Timeout(300.0, connect=5.0)
MAX_SOURCE_TEXT_BYTES = 5 * 1024 * 1024
NO_CONFIG = "no_configurado"
NO_RESPONSE = "no_responde"
USED = "usado"
SKIPPED = "omitido"


class LlmProbe:
    """Envuelve el cliente de FUNES y recuerda si el endpoint respondió.

    Los motores capturan los errores del LLM y siguen con reglas o plantillas; la ruta necesita
    saber si hubo respuestas para decidir entre 200 con aviso y 503.
    """

    def __init__(self, client: FunesClient) -> None:
        self._client = client
        self.answered = 0
        self.failures: list[str] = []

    async def chat_json(self, messages: list[dict[str, str]], schema: type[BaseModel], **kwargs: Any) -> Any:
        try:
            result = await self._client.chat_json(messages, schema, **kwargs)
        except LLMError as exc:
            self.failures.append(str(exc))
            raise
        except StructuredOutputError:
            self.answered += 1  # el endpoint respondió; la respuesta no sirvió
            raise
        self.answered += 1
        return result

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        try:
            result = await self._client.chat(messages, **kwargs)
        except LLMError as exc:
            self.failures.append(str(exc))
            raise
        self.answered += 1
        return result

    @property
    def status(self) -> str:
        if self.answered:
            return USED
        if self.failures:
            return NO_RESPONSE
        return SKIPPED

    @property
    def last_failure(self) -> str:
        return self.failures[-1] if self.failures else ""


def make_client(settings, http: httpx.AsyncClient | None = None) -> FunesClient:
    """Cliente del LLM con la configuración de la API. Falla con 503 si no está bien configurado.

    No se valida ni se usa el endpoint de embeddings: estas rutas no lo necesitan.
    """
    url = (settings.llm_base_url or "").strip()
    if not url:
        raise EngineUnavailable("El LLM de FUNES no está configurado: definí ALEPH_LLM_BASE_URL.")
    try:
        return FunesClient(
            llm_base_url=url, llm_model=settings.llm_model, llm_api_key=settings.llm_api_key or "",
            http=http, timeout=LLM_TIMEOUT, max_retries=1, backoff=0.5,
        )
    except NonLocalEndpointError as exc:
        raise EngineUnavailable(f"El LLM de FUNES no es un endpoint de la red propia: {exc}") from exc


def _unavailable(probe: LlmProbe, url: str) -> EngineUnavailable:
    return EngineUnavailable(
        f"El LLM de FUNES no respondió en {url}: {probe.last_failure[:300]}. "
        "Revisá que el servidor esté levantado y que ALEPH_LLM_BASE_URL apunte a él."
    )


# ---------------------------------------------------------------- NER


@dataclass
class NerRun:
    result: NerResult
    llm_status: str
    warnings: list[str] = field(default_factory=list)


async def _ner(text: str, settings, http, use_llm: bool) -> NerRun:
    if not use_llm:
        return NerRun(await extract_entities(text, None, use_llm=False), SKIPPED)
    try:
        client = make_client(settings, http)
    except EngineUnavailable as exc:
        rules = await extract_entities(text, None, use_llm=False)
        return NerRun(rules, NO_CONFIG, [f"{exc.message} Se entregan solo las entidades de reglas."])
    probe = LlmProbe(client)
    try:
        result = await extract_entities(text, probe, use_llm=True)  # type: ignore[arg-type]
    finally:
        await client.aclose()
    if probe.status == NO_RESPONSE:
        message = f"El LLM no respondió: {probe.last_failure[:300]}. Se entregan solo las entidades de reglas."
        return NerRun(result, NO_RESPONSE, [message])
    return NerRun(result, probe.status)


def run_ner(text: str, *, settings, http, use_llm: bool) -> NerRun:
    return asyncio.run(_ner(text, settings, http, use_llm))


def persist_ner_source(session: Session, case_id: int, user_id: int | None, text: str, reference: str,
                       data_dir):
    return make_source(
        session, case_id, user_id, kind="funes", connector="funes-ner", reference=reference[:2000],
        payload=text.encode("utf-8"), data_dir=data_dir, suffix=".txt",
    )


# ---------------------------------------------------------------- afirmaciones y contradicciones


async def _claims(sources: list[tuple[str, str]], settings, http, *, gazetteer: dict[str, tuple[float, float]],
                  utc_offset_hours: float) -> tuple[list[Claim], list[str]]:
    client = make_client(settings, http)  # sin LLM no hay afirmaciones que extraer: 503
    probe = LlmProbe(client)
    claims: list[Claim] = []
    warnings: list[str] = []
    try:
        for label, text in sources:
            extraction = await extract_claims(
                text, label, probe, gazetteer=gazetteer or None, utc_offset_hours=utc_offset_hours,
            )
            claims.extend(extraction.claims)
            warnings.extend(extraction.warnings)
    finally:
        await client.aclose()
    if probe.status == NO_RESPONSE:
        raise _unavailable(probe, settings.llm_base_url)
    return claims, warnings


def run_claims(sources: list[tuple[str, str]], *, settings, http, gazetteer: dict[str, tuple[float, float]],
               utc_offset_hours: float) -> tuple[list[Claim], list[str]]:
    return asyncio.run(_claims(sources, settings, http, gazetteer=gazetteer, utc_offset_hours=utc_offset_hours))


def detect(claims: list[Claim], *, max_speed_kmh: float) -> list[Contradiction]:
    if not claims:
        return []
    return detect_contradictions(claims, ContradictionConfig(max_speed_kmh=max_speed_kmh))


async def _explain(contradictions: list[Contradiction], claims: list[Claim], settings, http) -> list[Contradiction]:
    try:
        client = make_client(settings, http)
    except EngineUnavailable:
        return await explain_contradictions(contradictions, claims, None)  # redacción por plantilla
    probe = LlmProbe(client)
    try:
        return await explain_contradictions(contradictions, claims, probe)  # type: ignore[arg-type]
    finally:
        await client.aclose()


def explain(contradictions: list[Contradiction], claims: list[Claim], *, settings, http) -> list[Contradiction]:
    if not contradictions:
        return []
    return asyncio.run(_explain(contradictions, claims, settings, http))


# ---------------------------------------------------------------- informe


async def _draft(case_data: CaseData, settings, http) -> tuple[ReportDraft, LlmProbe]:
    client = make_client(settings, http)  # 503 si no está configurado
    probe = LlmProbe(client)
    try:
        draft = await draft_report(case_data, probe)  # type: ignore[arg-type]
    finally:
        await client.aclose()
    if probe.status == NO_RESPONSE:
        raise _unavailable(probe, settings.llm_base_url)
    return draft, probe


def run_report(case_data: CaseData, *, settings, http) -> tuple[ReportDraft, LlmProbe]:
    return asyncio.run(_draft(case_data, settings, http))


def build_case_data(session: Session, case, *, include_pending: bool,
                    claims: list[Claim] | None = None) -> CaseData:
    """Lo que el informe puede citar: entidades, relaciones, hipótesis de MENARD, fuentes y contradicciones."""
    statuses = ("confirmed", "proposed") if include_pending else ("confirmed",)
    entities = session.execute(
        select(Entity).where(Entity.case_id == case.id, Entity.status.in_(statuses)).order_by(Entity.id)
    ).scalars().all()
    ids = {e.id for e in entities}
    entity_records = [
        EntityRecord(type=e.type, label=e.label, props=e.props or {}, confidence=e.confidence, ref=f"e{e.id}")
        for e in entities
    ]
    relations = session.execute(
        select(Relation).where(Relation.case_id == case.id, Relation.status.in_(statuses)).order_by(Relation.id)
    ).scalars().all()
    relation_records = [
        RelationRecord(src_ref=f"e{r.src_id}", dst_ref=f"e{r.dst_id}", type=r.type, props=r.props or {},
                       confidence=r.confidence)
        for r in relations if r.src_id in ids and r.dst_id in ids
    ]
    review = ("pending", "confirmed") if include_pending else ("confirmed",)
    pairs: list[PairResult] = []
    for link in session.execute(
        select(AccountLink).where(AccountLink.case_id == case.id, AccountLink.review_status.in_(review))
        .order_by(AccountLink.id)
    ).scalars():
        try:
            pairs.append(PairResult.model_validate(link.signals))
        except ValueError:
            continue
    sources = session.execute(select(Source).where(Source.case_id == case.id).order_by(Source.id)).scalars().all()
    source_refs = [
        SourceRef(id=f"S{s.id}", title=(s.reference or f"fuente {s.id} ({s.kind})")[:200],
                  reference=s.reference, reliability=f"{s.reliability}{s.credibility}")
        for s in sources
    ]
    return CaseData(
        title=case.name, purpose=case.legal_basis or "", entities=entity_records, relations=relation_records,
        pairs=pairs, contradictions=detect(claims or [], max_speed_kmh=900.0), sources=source_refs,
    )


def gazetteer_from(places: dict[str, list[float]]) -> dict[str, tuple[float, float]]:
    return {name: (float(coords[0]), float(coords[1])) for name, coords in places.items() if len(coords) == 2}


def read_source_text(source: Source, data_dir: str | Path) -> str:
    """Texto del crudo de una fuente, verificando su hash antes de usarlo (como la descarga del crudo)."""
    if not source.raw_path:
        raise Invalid(f"La fuente {source.id} no tiene un crudo guardado para analizar.")
    base = Path(data_dir).resolve()
    path = (base / source.raw_path).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise Invalid(f"El crudo de la fuente {source.id} no está en el almacenamiento.")
    with path.open("rb") as handle:
        data = handle.read(MAX_SOURCE_TEXT_BYTES + 1)
    if len(data) > MAX_SOURCE_TEXT_BYTES:
        raise Invalid("La fuente supera el tamaño máximo para analizar (5 MB).")
    if source.sha256 and hashlib.sha256(data).hexdigest() != source.sha256:
        raise Conflict("El archivo guardado no coincide con el hash registrado: la evidencia fue alterada.")
    return data.decode("utf-8", errors="replace")
