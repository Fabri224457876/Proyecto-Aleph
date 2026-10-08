"""FUNES: NER, contradicciones y borrador de informe con el LLM local del cluster.

Qué devuelve cada uno cuando el LLM no está o no responde:
- NER: 201 con lo que sale por reglas, `llm_status` y un aviso. Lo que propone el LLM llega como
  propuesta; nada entra al grafo como confirmado.
- Contradicciones: sin fuentes de texto no hace falta LLM (200). Con fuentes, el LLM extrae las
  afirmaciones y si no responde, 503.
- Informe: necesita el LLM; si no está o no responde, 503 con el motivo.
"""

import hashlib
from typing import Annotated

import httpx
from fastapi import Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from aleph.core.models import Entity, Relation, Source
from aleph.funes.contradictions import Claim, Contradiction
from aleph.funes.ner import DateMention, Discarded

from .. import auditlog
from ..deps import DB, CurrentUser, ReadCase, SettingsDep, WriteCase
from ..routing import make_router
from ..schemas import EntityOut, RelationOut
from ..services import funes as funes_service
from ..services.outbound import llm_http
from ..services.proposals import PersistSummary, persist_graph, record_job
from ..services.util import get_in_case

router = make_router(prefix="/cases/{case_id}/funes", tags=["FUNES"])
LlmDep = Annotated[httpx.AsyncClient | None, Depends(llm_http)]
MAX_TEXT_CHARS = 200_000


class NerIn(BaseModel):
    text: str | None = Field(default=None, max_length=MAX_TEXT_CHARS, description="Texto a analizar")
    source_id: int | None = Field(default=None, description="O bien, una fuente del caso con texto guardado")
    use_llm: bool = Field(default=True, description="False: solo reglas, sin consultar al LLM")

    @model_validator(mode="after")
    def _one_input(self):
        if (self.text is None) == (self.source_id is None):
            raise ValueError("Indicá un texto o un id de fuente, pero no los dos.")
        if self.text is not None and not self.text.strip():
            raise ValueError("El texto está vacío.")
        return self


class NerOut(BaseModel):
    source_id: int
    llm_status: str = Field(description="usado | no_responde | no_configurado | omitido")
    warnings: list[str] = Field(default_factory=list)
    persisted: PersistSummary
    entities: list[EntityOut]
    relations: list[RelationOut]
    dates: list[DateMention] = Field(default_factory=list, description="Fechas de reglas (para la línea de tiempo)")
    discarded: list[Discarded] = Field(default_factory=list)


class ContradictionsIn(BaseModel):
    source_ids: list[int] = Field(default_factory=list, max_length=20,
                                  description="Fuentes con texto: el LLM extrae sus afirmaciones")
    claims: list[Claim] = Field(default_factory=list, description="Afirmaciones ya estructuradas (sin LLM)")
    gazetteer: dict[str, list[float]] = Field(default_factory=dict,
                                              description="Lugar -> [latitud, longitud], para las coordenadas")
    utc_offset_hours: float = Field(default=-3.0, ge=-12, le=14, description="Huso de las horas sin zona")
    max_speed_kmh: float = Field(default=900.0, gt=0, le=20000, description="Velocidad máxima de traslado")

    @model_validator(mode="after")
    def _some_input(self):
        if not self.source_ids and not self.claims:
            raise ValueError("Indicá fuentes con texto o afirmaciones estructuradas.")
        return self


class ContradictionsOut(BaseModel):
    llm_status: str
    claims: list[Claim]
    contradictions: list[Contradiction]
    warnings: list[str] = Field(default_factory=list)


class ReportIn(BaseModel):
    title: str = Field(default="", max_length=200, description="Por defecto, el nombre del caso")
    include_pending: bool = Field(default=False, description="Incluir propuestas e hipótesis pendientes")
    claims: list[Claim] = Field(default_factory=list, description="Afirmaciones con contradicciones a citar")


class ReportOut(BaseModel):
    markdown: str
    generated_by: str = Field(description="llm | plantilla")
    invalid_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@router.post(
    "/ner",
    response_model=NerOut,
    status_code=201,
    summary="Extraer entidades y relaciones de un texto o una fuente (propuestas)",
    responses={400: {"description": "Entrada inválida"}, 409: {"description": "La fuente fue alterada"}},
)
def ner(body: NerIn, case: WriteCase, session: DB, user: CurrentUser, settings: SettingsDep,
        llm: LlmDep) -> NerOut:
    if body.source_id is not None:
        source = get_in_case(session, Source, case.id, body.source_id, "La fuente")
        text = funes_service.read_source_text(source, settings.data_dir)
        reference = f"fuente {source.id}: {source.reference or source.kind}"
    else:
        text = body.text or ""
        reference = "texto pegado por el analista"
    run = funes_service.run_ner(text, settings=settings, http=llm, use_llm=body.use_llm)
    source_new = funes_service.persist_ner_source(session, case.id, user.id, text, reference, settings.data_dir)
    summary, _ = persist_graph(session, case.id, run.result.entities, run.result.relations,
                               source_id=source_new.id)
    warnings = [*run.result.warnings, *run.warnings]
    record_job(
        session, case.id, "funes.ner", params={"source_id": body.source_id, "use_llm": body.use_llm},
        result={"llm_status": run.llm_status, "entidades": len(summary.entity_ids),
                "relaciones": len(summary.relation_ids)}, user_id=user.id,
    )
    auditlog.record(
        session, "funes.ner", user_id=user.id, case_id=case.id, target=f"source:{source_new.id}",
        detail={"llm_status": run.llm_status, "entities_created": summary.entities_created,
                "relations_created": summary.relations_created, "discarded": len(run.result.discarded),
                "chars": len(text)},
    )
    session.commit()
    entities = session.execute(
        select(Entity).where(Entity.id.in_(summary.entity_ids)).order_by(Entity.id)
    ).scalars().all() if summary.entity_ids else []
    relations = session.execute(
        select(Relation).where(Relation.id.in_(summary.relation_ids)).order_by(Relation.id)
    ).scalars().all() if summary.relation_ids else []
    return NerOut(
        source_id=source_new.id, llm_status=run.llm_status, warnings=warnings, persisted=summary,
        entities=[EntityOut.model_validate(e) for e in entities],
        relations=[RelationOut.model_validate(r) for r in relations],
        dates=run.result.dates, discarded=run.result.discarded,
    )


@router.post(
    "/contradictions",
    response_model=ContradictionsOut,
    summary="Detectar contradicciones espacio-temporales y de valores entre fuentes",
    responses={
        400: {"description": "Entrada inválida"},
        503: {"description": "Hay fuentes de texto y el LLM no está o no responde"},
    },
)
def contradictions(body: ContradictionsIn, case: ReadCase, session: DB, user: CurrentUser,
                   settings: SettingsDep, llm: LlmDep) -> ContradictionsOut:
    sources: list[tuple[str, str]] = []
    for source_id in body.source_ids:
        source = get_in_case(session, Source, case.id, source_id, "La fuente")
        sources.append((source.reference or f"fuente {source.id}",
                        funes_service.read_source_text(source, settings.data_dir)))
    extracted: list[Claim] = []
    warnings: list[str] = []
    if sources:
        extracted, warnings = funes_service.run_claims(
            sources, settings=settings, http=llm, gazetteer=funes_service.gazetteer_from(body.gazetteer),
            utc_offset_hours=body.utc_offset_hours,
        )
    claims = [*extracted, *body.claims]
    found = funes_service.detect(claims, max_speed_kmh=body.max_speed_kmh)
    explained = funes_service.explain(found, claims, settings=settings, http=llm)
    llm_status = "usado" if sources else "omitido"
    auditlog.record(
        session, "funes.contradictions", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
        detail={"sources": body.source_ids, "claims": len(claims), "contradictions": len(explained),
                "severities": [c.severity for c in explained]},
    )
    session.commit()
    return ContradictionsOut(llm_status=llm_status, claims=claims, contradictions=explained, warnings=warnings)


@router.post(
    "/report",
    response_model=ReportOut,
    summary="Borrador de informe de inteligencia en Markdown (queda auditado)",
    responses={503: {"description": "El LLM no está configurado o no responde"}},
)
def report(body: ReportIn, case: ReadCase, session: DB, user: CurrentUser, settings: SettingsDep,
           llm: LlmDep) -> ReportOut:
    data = funes_service.build_case_data(session, case, include_pending=body.include_pending, claims=body.claims)
    if body.title.strip():
        data.title = body.title.strip()
    draft, _probe = funes_service.run_report(data, settings=settings, http=llm)
    digest = hashlib.sha256(draft.markdown.encode("utf-8")).hexdigest()
    auditlog.record(
        session, "funes.report", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
        detail={"generated_by": draft.generated_by, "include_pending": body.include_pending,
                "sha256": digest, "invalid_ids": draft.invalid_ids[:50]},
    )
    session.commit()
    return ReportOut(markdown=draft.markdown, generated_by=draft.generated_by,
                     invalid_ids=draft.invalid_ids, warnings=draft.warnings)
