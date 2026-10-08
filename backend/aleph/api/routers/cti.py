"""CTI: IOCs de un texto, enriquecimiento de indicadores, técnicas ATT&CK y datachunks sin LLM.

Todo lo que se incorpora al grafo (IOCs, enriquecimiento) entra como propuesta. Los datachunks no
se guardan: son la vista previa que el analista arrastra al expediente con POST /findings.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel, Field

from aleph.core.models import Entity
from aleph.core.schemas import Datachunk
from aleph.cti.attack import load_index
from aleph.cti.ioc import Discarded, TechniqueMention

from .. import auditlog
from ..deps import DB, AnyUser, CurrentUser, ReadCase, SettingsDep, WriteCase
from ..errors import Invalid
from ..routing import make_router
from ..schemas import Message
from ..services import cti as cti_service
from ..services.outbound import enrichment_providers
from ..services.proposals import PersistSummary, record_job
from ..services.util import get_in_case

router = make_router(prefix="/cases/{case_id}", tags=["CTI"])
attack = make_router(prefix="/attack", tags=["CTI"])
ProvidersDep = Annotated[list[Any] | None, Depends(enrichment_providers)]
MAX_TEXT_CHARS = 200_000


class IocExtractIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS, description="Texto libre: informe, nota, pegado")


class IocExtractOut(BaseModel):
    source_id: int
    persisted: PersistSummary
    techniques: list[TechniqueMention] = Field(default_factory=list,
                                               description="Técnicas ATT&CK mencionadas: sugerencias, no vínculos")
    discarded: list[Discarded] = Field(default_factory=list, description="Candidatos descartados, con el motivo")
    defanged_input: bool = False


class EnrichResultOut(BaseModel):
    provider: str
    status: str
    verdict: str
    labels: list[str] = Field(default_factory=list)
    message: str = ""
    retry_after: float | None = None


class EnrichOut(BaseModel):
    source_id: int
    indicator: dict[str, str]
    verdict: str = Field(description="El más severo entre los proveedores que respondieron")
    results: list[EnrichResultOut]
    persisted: PersistSummary


class TechniqueLinkIn(BaseModel):
    technique_id: str = Field(min_length=3, max_length=12, description="ID ATT&CK, p. ej. T1566.002")
    score: float | None = Field(default=None, ge=0, le=100, description="Puntaje 0-100 para la capa")
    comment: str = Field(default="", max_length=2000)


class TechniqueLinkOut(BaseModel):
    finding_id: int
    technique_id: str
    nombre: str
    score: float | None
    comment: str
    estado: str
    created: bool


class TechniqueOut(BaseModel):
    id: str
    nombre: str
    tacticas: list[str]
    nombres_tacticas: list[str]
    subtecnica: bool
    padre: str | None
    url: str


class ChunksIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    page_url: str = Field(default="", max_length=2000, description="URL de la página, si la hay")
    page_title: str = Field(default="", max_length=500)
    platform: str = Field(default="generic", max_length=100)
    captured_at: datetime | None = Field(default=None, description="Por defecto, ahora (UTC)")


@router.post(
    "/ioc/extract",
    response_model=IocExtractOut,
    status_code=201,
    summary="Extraer IOCs de un texto y guardarlos como propuestas",
)
def extract_iocs(body: IocExtractIn, case: WriteCase, session: DB, user: CurrentUser, settings: SettingsDep):
    result = cti_service.extract_iocs_to_graph(session, case, body.text, user.id, settings.data_dir)
    summary: PersistSummary = result["summary"]
    auditlog.record(
        session, "ioc.extract", user_id=user.id, case_id=case.id, target=f"source:{result['source_id']}",
        detail={"entities_created": summary.entities_created, "entities_existing": summary.entities_existing,
                "techniques_mentioned": len(result["techniques"]), "discarded": len(result["discarded"]),
                "chars": len(body.text)},
    )
    session.commit()
    return IocExtractOut(source_id=result["source_id"], persisted=summary, techniques=result["techniques"],
                         discarded=result["discarded"], defanged_input=result["defanged_input"])


@router.post(
    "/entities/{entity_id}/enrich",
    response_model=EnrichOut,
    status_code=201,
    summary="Consultar proveedores CTI sobre un indicador; lo nuevo queda como propuesta",
    responses={
        400: {"model": Message, "description": "Ningún proveedor consulta ese tipo de entidad"},
    },
)
def enrich(entity_id: int, case: WriteCase, session: DB, user: CurrentUser, settings: SettingsDep,
           providers: ProvidersDep) -> EnrichOut:
    entity: Entity = get_in_case(session, Entity, case.id, entity_id, "La entidad")
    report, saved = cti_service.enrich_entity(session, case, entity, user.id, settings.data_dir,
                                              providers=providers)
    summary: PersistSummary = saved["summary"]
    statuses = {r.provider: r.status for r in report.results}
    record_job(
        session, case.id, "enrich", params={"entity_id": entity.id, "tipo": entity.type},
        result={"source_id": saved["source_id"], "estados": statuses}, user_id=user.id,
    )
    auditlog.record(
        session, "entity.enrich", user_id=user.id, case_id=case.id, target=f"entity:{entity.id}",
        detail={"providers": statuses, "verdict": report.verdict, "source_id": saved["source_id"],
                "entities_created": summary.entities_created, "relations_created": summary.relations_created},
    )
    session.commit()
    return EnrichOut(
        source_id=saved["source_id"],
        indicator={"id": str(entity.id), "tipo": entity.type, "valor": entity.label},
        verdict=report.verdict,
        results=[EnrichResultOut(provider=r.provider, status=r.status, verdict=r.verdict, labels=r.labels,
                                 message=r.message, retry_after=r.retry_after) for r in report.results],
        persisted=summary,
    )


@router.post(
    "/captures/chunks",
    response_model=list[Datachunk],
    summary="Datachunks de un texto: IOCs y reglas argentinas de FUNES (sin LLM, sin guardar nada)",
)
def captures_chunks(body: ChunksIn, case: ReadCase, session: DB, user: CurrentUser):
    captured = body.captured_at or datetime.now(UTC)
    chunks = cti_service.chunks_from_text(
        body.text, page_url=body.page_url.strip(), page_title=body.page_title.strip(),
        platform=body.platform.strip().lower(), captured_at=captured,
    )
    auditlog.record(session, "captures.chunks", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
                    detail={"chars": len(body.text), "chunks": len(chunks)})
    session.commit()
    return chunks


@router.get("/attack/layer", summary="Capa para ATT&CK Navigator con las técnicas vinculadas al caso")
def attack_layer(case: ReadCase, session: DB, user: CurrentUser,
                 include_pending: Annotated[bool, Query(description="Incluir técnicas propuestas")] = False):
    return cti_service.attack_layer(session, case, include_pending=include_pending)


@router.post(
    "/attack/techniques",
    response_model=TechniqueLinkOut,
    status_code=201,
    summary="Vincular una técnica ATT&CK al caso (decisión del analista)",
    responses={400: {"model": Message, "description": "El ID no existe en ATT&CK"}},
)
def link_technique(body: TechniqueLinkIn, case: WriteCase, session: DB, user: CurrentUser) -> TechniqueLinkOut:
    finding, created = cti_service.link_technique(
        session, case, body.technique_id, score=body.score, comment=body.comment, user_id=user.id,
    )
    tech = load_index().get(finding.value)
    auditlog.record(
        session, "attack.link" if created else "attack.update", user_id=user.id, case_id=case.id,
        target=f"finding:{finding.id}", detail={"technique": finding.value, "score": body.score},
    )
    session.commit()
    return TechniqueLinkOut(
        finding_id=finding.id, technique_id=finding.value, nombre=tech.name if tech else "",
        score=(finding.chunk or {}).get("score"), comment=finding.note,
        estado=(finding.chunk or {}).get("estado", "confirmada"), created=created,
    )


@attack.get("/techniques", response_model=list[TechniqueOut], summary="Buscar técnicas ATT&CK por ID o texto")
def search_techniques(user: AnyUser,
                      q: Annotated[str, Query(min_length=1, max_length=100,
                                              description="ID (T1566), prefijo de ID o texto del nombre")],
                      limit: Annotated[int, Query(ge=1, le=100)] = 20):
    if not q.strip():
        raise Invalid("La búsqueda no puede estar vacía.")
    return [TechniqueOut(**t) for t in cti_service.search_techniques(q, limit)]
