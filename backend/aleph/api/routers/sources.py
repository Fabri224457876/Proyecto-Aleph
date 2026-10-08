"""Fuentes: la procedencia de cada dato, con su valoración Admiralty y el hash del crudo."""

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from aleph.core.models import Source

from .. import auditlog
from ..deps import DB, CurrentUser, Paging, ReadCase, SettingsDep, WriteCase, page_of, paginate
from ..presenters import source_out
from ..routing import make_router
from ..schemas import CREDIBILITY, RELIABILITY, SOURCE_KINDS, Page, SourceCreate, SourceOut
from ..services.util import as_utc, get_in_case

router = make_router(prefix="/cases/{case_id}/sources", tags=["fuentes"])

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
_CHUNK = 1024 * 1024


@router.post("", response_model=SourceOut, status_code=201, summary="Registrar una fuente")
def create_source(body: SourceCreate, case: WriteCase, session: DB, user: CurrentUser):
    data = body.model_dump()
    data["sha256"] = data["sha256"].lower()
    retrieved_at = as_utc(data.pop("retrieved_at"))
    source = Source(case_id=case.id, created_by=user.id, **data)
    if retrieved_at is not None:
        source.retrieved_at = retrieved_at
    session.add(source)
    session.flush()
    auditlog.record(
        session, "source.create", user_id=user.id, case_id=case.id, target=f"source:{source.id}",
        detail={"kind": source.kind, "reference": source.reference, "sha256": source.sha256,
                "admiralty": f"{source.reliability}{source.credibility}"},
    )
    session.commit()
    return source_out(source)


@router.post("/upload", response_model=SourceOut, status_code=201,
             summary="Subir un archivo como fuente (guarda el crudo y su sha256)")
def upload_source(
    case: WriteCase, session: DB, user: CurrentUser, settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Archivo aportado por el operador")],
    reliability: Annotated[str, Form(description="Fiabilidad de la fuente, A-F")] = "F",
    credibility: Annotated[str, Form(description="Credibilidad del dato, 1-6")] = "6",
    reference: Annotated[str, Form(description="Descripción u origen; por defecto el nombre del archivo")] = "",
):
    reliability, credibility = reliability.strip().upper(), credibility.strip()
    if reliability not in RELIABILITY:
        raise HTTPException(status_code=422, detail="Fiabilidad inválida: el Admiralty Code admite de A a F.")
    if credibility not in CREDIBILITY:
        raise HTTPException(status_code=422, detail="Credibilidad inválida: el Admiralty Code admite de 1 a 6.")

    original = os.path.basename((file.filename or "").replace("\\", "/"))[:255]
    suffix = Path(original).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ""
    rel_dir = Path("cases") / str(case.id) / "sources"
    target_dir = Path(settings.data_dir) / rel_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = target_dir / f".upload-{uuid.uuid4().hex}.tmp"
    digest, size = hashlib.sha256(), 0
    try:
        with tmp_path.open("wb") as out:
            while chunk := file.file.read(_CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="El archivo supera el tamaño máximo permitido (200 MB).")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="El archivo está vacío.")
        sha = digest.hexdigest()
        # El nombre en disco sale del hash, nunca del nombre que mandó el cliente
        rel_path = rel_dir / f"{sha}{suffix}"
        final_path = Path(settings.data_dir) / rel_path
        if final_path.exists():
            tmp_path.unlink()
        else:
            tmp_path.replace(final_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    source = Source(
        case_id=case.id, kind="upload", reference=reference.strip() or original or sha,
        reliability=reliability, credibility=credibility, sha256=sha, raw_path=rel_path.as_posix(),
        created_by=user.id,
    )
    session.add(source)
    session.flush()
    auditlog.record(
        session, "source.upload", user_id=user.id, case_id=case.id, target=f"source:{source.id}",
        detail={"filename": original, "sha256": sha, "bytes": size, "admiralty": f"{reliability}{credibility}"},
    )
    session.commit()
    return source_out(source)


@router.get("", response_model=Page[SourceOut], summary="Listar fuentes")
def list_sources(
    case: ReadCase, session: DB, page: Paging,
    kind: Annotated[str | None, Query(description=f"Uno de: {', '.join(SOURCE_KINDS)}")] = None,
):
    stmt = select(Source).where(Source.case_id == case.id).order_by(Source.id.desc())
    if kind:
        stmt = stmt.where(Source.kind == kind)
    items, total = paginate(session, stmt, page)
    return page_of([source_out(s) for s in items], total, page)


@router.get("/{source_id}", response_model=SourceOut, summary="Ver fuente")
def get_source(source_id: int, case: ReadCase, session: DB):
    return source_out(get_in_case(session, Source, case.id, source_id, "La fuente"))


@router.get("/{source_id}/raw", summary="Descargar el crudo de una fuente (verifica el hash; queda auditado)",
            response_class=FileResponse)
def download_raw(source_id: int, case: ReadCase, session: DB, user: CurrentUser, settings: SettingsDep):
    source = get_in_case(session, Source, case.id, source_id, "La fuente")
    if not source.raw_path:
        raise HTTPException(status_code=404, detail="Esta fuente no tiene un crudo guardado.")
    base = Path(settings.data_dir).resolve()
    path = (base / source.raw_path).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(status_code=404, detail="El crudo de esta fuente no está en el almacenamiento.")
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    intact = not source.sha256 or digest.hexdigest() == source.sha256
    auditlog.record(session, "source.download", user_id=user.id, case_id=case.id, target=f"source:{source.id}",
                    detail={"sha256": source.sha256, "intact": intact})
    session.commit()
    if not intact:
        raise HTTPException(
            status_code=409, detail="El archivo guardado no coincide con el hash registrado: la evidencia fue alterada."
        )
    return FileResponse(
        path, media_type="application/octet-stream", filename=f"aleph-source-{source.id}{path.suffix}",
        headers={"X-Aleph-SHA256": source.sha256},
    )
