"""TLÖN: hipótesis de vínculos con un modelo del caso (estilo JEPA) revisadas por FUNES."""

from typing import Annotated

import httpx
from fastapi import Depends
from sqlalchemy import select

from aleph.core.models import Job

from .. import auditlog
from ..deps import DB, CurrentUser, ReadCase, SettingsDep, WriteCase
from ..errors import NotFound
from ..routing import make_router
from ..services import tlon as tlon_service
from ..services.outbound import llm_http
from ..services.proposals import record_job

router = make_router(prefix="/cases/{case_id}/tlon", tags=["TLÖN"])
LlmDep = Annotated[httpx.AsyncClient | None, Depends(llm_http)]


@router.post("/run", response_model=tlon_service.TlonRunOut,
             summary="Entrenar el modelo del caso y proponer vínculos faltantes (quedan como propuestas)")
def run(body: tlon_service.TlonRunIn, case: WriteCase, session: DB, user: CurrentUser, settings: SettingsDep,
        llm: LlmDep) -> tlon_service.TlonRunOut:
    out = tlon_service.run(session, case, user.id, body, settings=settings, http=llm)
    job = record_job(session, case.id, "tlon", params=body.model_dump(), user_id=user.id,
                     result=out.model_dump(mode="json"))
    auditlog.record(
        session, "tlon.run", user_id=user.id, case_id=case.id, target=f"job:{job.id}",
        detail={"status": out.status, "nodes": out.nodes, "edges": out.edges, "hypotheses": len(out.hypotheses),
                "created_relations": out.created_relations, "llm_status": out.llm_status,
                "embeddings": out.embeddings_status, "source_id": out.source_id, "quality": out.quality},
    )
    session.commit()
    return out


@router.get("/latest", response_model=tlon_service.TlonRunOut, summary="Resultado de la última corrida de TLÖN")
def latest(case: ReadCase, session: DB) -> tlon_service.TlonRunOut:
    job = session.execute(select(Job).where(Job.case_id == case.id, Job.kind == "tlon", Job.status == "done")
                          .order_by(Job.id.desc()).limit(1)).scalars().first()
    if job is None:
        raise NotFound("TLÖN todavía no se corrió en este caso.")
    return tlon_service.TlonRunOut.model_validate(job.result)
