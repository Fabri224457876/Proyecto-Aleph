"""Caja de herramientas OSINT: GET /api/tools y POST /api/cases/{id}/tools/{name}/run.

Lo que devuelve una herramienta se guarda como propuestas (entidades y relaciones) con una
`Source` que conserva el resultado completo y su hash. Nada entra al grafo como confirmado.
"""

from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from .. import auditlog
from ..deps import DB, AnyUser, CurrentUser, SettingsDep, WriteCase
from ..errors import EngineFailure
from ..routing import make_router
from ..schemas import Message
from ..services.collect import MAX_UPLOAD_BYTES, parse_params
from ..services.outbound import outbound_http
from ..services.proposals import (
    PersistSummary,
    canonical_json,
    make_source,
    persist_graph,
    record_job,
)
from ..services.tools import catalog, read_limited, run_tool

router = make_router(prefix="/tools", tags=["herramientas OSINT"])
case_router = make_router(prefix="/cases/{case_id}/tools", tags=["herramientas OSINT"])
OutboundDep = Annotated[httpx.AsyncClient | None, Depends(outbound_http)]


class ToolOut(BaseModel):
    name: str
    title: str
    description: str
    inputs: list[str]
    network: bool = Field(description="Consulta servicios externos (siempre en solo lectura)")
    requires_key: bool
    key_settings: list[str]
    is_async: bool


class ToolRunOut(BaseModel):
    job_id: int
    tool: str
    source_id: int
    result: dict[str, Any] = Field(description="Resultado completo de la herramienta")
    persisted: PersistSummary
    ignored_params: list[str] = Field(default_factory=list)


@router.get("", response_model=list[ToolOut], summary="Herramientas OSINT disponibles (solo lectura)")
def list_tools(user: AnyUser):
    return [
        ToolOut(name=t.name, title=t.title, description=t.description, inputs=list(t.inputs),
                network=t.network, requires_key=t.requires_key, key_settings=list(t.key_settings),
                is_async=t.is_async)
        for t in catalog()
    ]


@case_router.post(
    "/{name}/run",
    response_model=ToolRunOut,
    status_code=201,
    summary="Ejecutar una herramienta OSINT y guardar sus resultados como propuestas",
    responses={
        404: {"model": Message, "description": "No existe la herramienta"},
        413: {"model": Message, "description": "El archivo supera el tamaño máximo"},
        502: {"model": Message, "description": "La herramienta no pudo consultar su fuente"},
    },
)
def run_tool_route(
    name: str,
    case: WriteCase,
    session: DB,
    user: CurrentUser,
    settings: SettingsDep,
    outbound: OutboundDep,
    target: Annotated[str, Form(max_length=2048, description="Dato a analizar: dominio, IP, wallet, CVE...")] = "",
    params: Annotated[str, Form(description="Argumentos opcionales de la herramienta, como objeto JSON")] = "{}",
    file: Annotated[UploadFile | None, File(description="Archivo para herramientas que analizan bytes")] = None,
) -> ToolRunOut:
    raw_params = parse_params(params)
    payload = read_limited(file.file, MAX_UPLOAD_BYTES) if file is not None else None
    filename = (file.filename or "") if file is not None else ""
    storage_dir = Path(settings.data_dir) / "cases" / str(case.id) / "evidence"
    job_params = {"tool": name, "target": target.strip()[:200], "params": raw_params, "archivo": filename[:255]}
    try:
        run = run_tool(
            name, target=target, params=raw_params, payload=payload, filename=filename, actor=user.username,
            storage_dir=str(storage_dir), client=outbound,
        )
    except EngineFailure as exc:
        session.rollback()
        record_job(session, case.id, "tool", params=job_params, result={}, user_id=user.id,
                   status="failed", error=exc.message)
        auditlog.record(session, "tool.failed", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
                        detail={"tool": name, "error": exc.message[:500]})
        session.commit()
        raise

    reference = target.strip() or filename or name
    source = make_source(
        session, case.id, user.id, kind="manual", connector=f"osint:{run.tool}", reference=reference,
        payload=canonical_json({"herramienta": run.tool, "resultado": run.result}), data_dir=settings.data_dir,
    )
    summary, _ = persist_graph(session, case.id, run.entities, run.relations, source_id=source.id)
    job = record_job(
        session, case.id, "tool", params=job_params, user_id=user.id,
        result={"source_id": source.id, "entidades": len(summary.entity_ids), "relaciones": len(summary.relation_ids)},
    )
    auditlog.record(
        session, "tool.run", user_id=user.id, case_id=case.id, target=f"source:{source.id}",
        detail={"tool": run.tool, "target": target.strip()[:200], "job_id": job.id,
                "entities_created": summary.entities_created, "relations_created": summary.relations_created,
                "ignored": run.ignored_params},
    )
    session.commit()
    return ToolRunOut(job_id=job.id, tool=run.tool, source_id=source.id, result=run.result,
                      persisted=summary, ignored_params=run.ignored_params)
