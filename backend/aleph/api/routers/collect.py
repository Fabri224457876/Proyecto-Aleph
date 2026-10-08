"""Recolección por conector: POST /api/cases/{id}/collect.

Los conectores en vivo se llaman con `connector` y `params` (JSON). Los de importación además
reciben el archivo en `file`. Multipart porque una misma ruta sirve a los dos casos.
"""

from typing import Annotated

import httpx
from fastapi import Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from aleph.core.models import Job

from .. import auditlog
from ..deps import DB, CurrentUser, SettingsDep, WriteCase
from ..errors import EngineFailure, Invalid
from ..routing import make_router
from ..schemas import Message
from ..services.collect import (
    check_required,
    declared_kwargs,
    ensure_configured,
    parse_params,
    resolve_connector,
    run_connector,
    store_upload,
)
from ..services.ingest import IngestSummary, ingest_collection
from ..services.outbound import outbound_http
from ..services.proposals import record_job

router = make_router(prefix="/cases/{case_id}", tags=["recolección"])
OutboundDep = Annotated[httpx.AsyncClient | None, Depends(outbound_http)]


class CollectOut(BaseModel):
    job_id: int
    connector: str
    ignored_params: list[str] = Field(
        default_factory=list,
        description="Claves que el conector no declara, o que solo fija la API (path, data)",
    )
    ingest: IngestSummary


@router.post(
    "/collect",
    response_model=CollectOut,
    status_code=201,
    summary="Ejecutar un conector y guardar lo recolectado (queda como trabajo y en la auditoría)",
    responses={
        404: {"model": Message, "description": "No existe el conector"},
        413: {"model": Message, "description": "El archivo supera el tamaño máximo"},
        502: {"model": Message, "description": "El conector falló (la falla queda registrada)"},
        503: {"model": Message, "description": "Falta configurar el conector o el módulo no está instalado"},
    },
)
def collect(
    case: WriteCase,
    session: DB,
    user: CurrentUser,
    settings: SettingsDep,
    outbound: OutboundDep,
    connector: Annotated[str, Form(min_length=1, max_length=64,
                                   description="Nombre del conector, p. ej. bluesky o generic_csv")],
    params: Annotated[str, Form(description="Parámetros del conector, como objeto JSON")] = "{}",
    file: Annotated[UploadFile | None, File(description="Archivo, solo para conectores de importación")] = None,
) -> CollectOut:
    cls = resolve_connector(connector)
    kwargs, ignored = declared_kwargs(cls, parse_params(params))
    check_required(cls, kwargs)
    ensure_configured(cls, settings)
    file_info: dict = {}
    if cls.mode == "import":
        if file is None:
            raise Invalid(f"El conector '{cls.name}' es de importación: subí el archivo en el campo 'file'.")
        stored = store_upload(file.file, file.filename, case.id, settings.data_dir)
        kwargs["path"] = str(stored.path)  # la ruta la pone la API; nunca viene del cliente
        file_info = {"nombre": stored.name, "sha256": stored.sha256, "bytes": stored.size}
    elif file is not None:
        raise Invalid(f"El conector '{cls.name}' no recibe archivos: quitá el campo 'file'.")

    job_params = {"connector": cls.name, "params": {k: v for k, v in kwargs.items() if k != "path"},
                  "ignored": ignored, "file": file_info}
    try:
        result = run_connector(cls, kwargs, client=outbound, settings=settings)
    except EngineFailure as exc:
        session.rollback()
        record_job(session, case.id, "collect", params=job_params, result={}, user_id=user.id,
                   status="failed", error=exc.message)
        auditlog.record(session, "collect.failed", user_id=user.id, case_id=case.id,
                        target=f"case:{case.id}", detail={"connector": cls.name, "error": exc.message[:500]})
        session.commit()
        raise

    summary = ingest_collection(session, case.id, result, user.id, data_dir=settings.data_dir)
    job: Job = record_job(
        session, case.id, "collect", params=job_params,
        result={"ingest": summary.model_dump(), "warnings": result.warnings}, user_id=user.id,
    )
    auditlog.record(
        session, "collect.run", user_id=user.id, case_id=case.id, target=f"job:{job.id}",
        detail={"connector": cls.name, "mode": cls.mode, "ignored": ignored, "job_id": job.id,
                "source_id": summary.source_id, "accounts": summary.accounts_created + summary.accounts_updated},
    )
    session.commit()
    return CollectOut(job_id=job.id, connector=cls.name, ignored_params=ignored, ingest=summary)
