"""Aleph Lens: captura desde la extensión, incisos del expediente y hallazgos."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select

from aleph.core.models import CaseSection, Finding
from aleph.core.schemas import (
    ENTITY_TYPES,
    CaptureBatch,
    FindingCreate,
    HandleLookupRequest,
    HandleLookupResponse,
    SectionOut,
)

from .. import auditlog
from ..deps import DB, CurrentUser, Paging, ReadCase, SettingsDep, WriteCase, page_of, paginate
from ..routing import make_router
from ..schemas import Out, Page, UTCDatetime
from ..services import lens
from ..services.util import get_in_case

router = make_router(prefix="/cases/{case_id}", tags=["lens"])


def _name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("El nombre del inciso no puede estar vacío.")
    return value


class SectionCreate(BaseModel):
    name: str = Field(max_length=100)
    position: int | None = Field(default=None, ge=0, description="Por defecto, al final")

    _clean = field_validator("name")(_name)


class SectionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    position: int | None = Field(default=None, ge=0)

    _clean = field_validator("name")(_name)


class FindingOut(Out):
    id: int
    case_id: int
    section_id: int | None
    entity_id: int | None
    source_id: int | None
    kind: str
    value: str
    chunk: dict[str, Any]
    note: str
    created_by: int | None
    created_at: UTCDatetime


class FindingCreated(FindingOut):
    entity_created: bool = False
    relation_id: int | None = None


class FindingUpdate(BaseModel):
    section_id: int | None = Field(default=None, description="Mandar `null` explícito lo deja sin clasificar")
    note: str | None = Field(default=None, max_length=10000)


# ---------------------------------------------------------------- captura

@router.post("/captures", response_model=lens.CaptureSummary, status_code=201,
             summary="Captura en vivo desde la extensión")
def capture(body: CaptureBatch, case: WriteCase, session: DB, user: CurrentUser, settings: SettingsDep):
    """Idempotente: la extensión puede reenviar publicaciones ya vistas. Las `interactions` quedan
    como relaciones entre las entidades de cuenta. `text_snippets` se conserva en el crudo de la fuente."""
    summary = lens.capture_batch(session, case.id, body, user.id, data_dir=settings.data_dir)
    session.commit()
    return summary


@router.post("/captures/lookup", response_model=HandleLookupResponse,
             summary="Qué sabe el caso de los handles visibles en pantalla")
def lookup(body: HandleLookupRequest, case: ReadCase, session: DB, user: CurrentUser):
    response = lens.lookup_handles(session, case.id, body)
    known = sum(1 for info in response.handles.values() if info.known)
    auditlog.record(session, "lens.lookup", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
                    detail={"platform": body.platform, "handles": len(body.handles), "known": known})
    session.commit()
    return response


# ---------------------------------------------------------------- incisos

@router.get("/sections", response_model=list[SectionOut], summary="Incisos del expediente, con contador de hallazgos")
def list_sections(case: ReadCase, session: DB, user: CurrentUser):
    sections, created = lens.ensure_default_sections(session, case.id)
    if created:  # casos anteriores a los incisos: se inicializan en la primera consulta
        auditlog.record(session, "section.defaults", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
                        detail={"sections": [s.name for s in sections]})
        session.commit()
    return lens.sections_out(session, case.id, sections)


@router.post("/sections", response_model=SectionOut, status_code=201, summary="Crear inciso")
def create_section(body: SectionCreate, case: WriteCase, session: DB, user: CurrentUser):
    sections, _ = lens.ensure_default_sections(session, case.id)
    if any(s.name.lower() == body.name.lower() for s in sections):
        raise HTTPException(status_code=409, detail="Ya existe un inciso con ese nombre en el caso.")
    position = body.position if body.position is not None else max((s.position for s in sections), default=-1) + 1
    section = CaseSection(case_id=case.id, name=body.name, position=position)
    session.add(section)
    session.flush()
    auditlog.record(session, "section.create", user_id=user.id, case_id=case.id, target=f"section:{section.id}",
                    detail={"name": section.name, "position": section.position})
    session.commit()
    return SectionOut(id=section.id, name=section.name, position=section.position, findings=0)


@router.patch("/sections/{section_id}", response_model=SectionOut, summary="Renombrar o reordenar un inciso")
def update_section(section_id: int, body: SectionUpdate, case: WriteCase, session: DB, user: CurrentUser):
    section = get_in_case(session, CaseSection, case.id, section_id, "El inciso")
    changes = {}
    if body.name is not None and body.name != section.name:
        clash = session.execute(
            select(CaseSection.id).where(
                CaseSection.case_id == case.id, CaseSection.id != section.id,
                func.lower(CaseSection.name) == body.name.lower(),
            )
        ).first()
        if clash:
            raise HTTPException(status_code=409, detail="Ya existe un inciso con ese nombre en el caso.")
        changes["name"] = {"from": section.name, "to": body.name}
        section.name = body.name
    if body.position is not None and body.position != section.position:
        changes["position"] = {"from": section.position, "to": body.position}
        section.position = body.position
    if changes:
        auditlog.record(session, "section.update", user_id=user.id, case_id=case.id,
                        target=f"section:{section.id}", detail={"changes": changes})
        session.commit()
    return lens.sections_out(session, case.id, [section])[0]


@router.delete("/sections/{section_id}", status_code=204,
               summary="Borrar inciso (sus hallazgos pasan a sin clasificar)")
def delete_section(section_id: int, case: WriteCase, session: DB, user: CurrentUser):
    lens.delete_section(session, case.id, section_id, user.id)
    session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- hallazgos

@router.post("/findings", response_model=FindingCreated, status_code=201,
             summary="Incorporar un datachunk al expediente")
def create_finding(body: FindingCreate, case: WriteCase, session: DB, user: CurrentUser):
    """Crea el hallazgo con su fuente (URL, hora y `sha256(quote + "\\n" + context)`).

    Si `chunk.kind` es un tipo de entidad, busca o crea la entidad (confirmada) y la vincula; con
    `attach_to_entity_id` además crea la relación `related_to`. Un chunk `text` soltado sobre una
    entidad queda como nota de esa entidad.
    """
    lens.ensure_default_sections(session, case.id)
    result = lens.create_finding(session, case.id, body, user.id)
    session.commit()
    out = FindingCreated.model_validate(result.finding)
    out.entity_created, out.relation_id = result.entity_created, result.relation_id
    return out


@router.get("/findings", response_model=Page[FindingOut], summary="Listar hallazgos")
def list_findings(
    case: ReadCase, session: DB, page: Paging,
    section_id: Annotated[int | None, Query(description="Solo los de este inciso")] = None,
    unclassified: Annotated[bool, Query(description="Solo los que no tienen inciso")] = False,
    entity_id: Annotated[int | None, Query()] = None,
    kind: Annotated[str | None, Query(description=f"text o uno de: {', '.join(ENTITY_TYPES)}")] = None,
    since: Annotated[datetime | None, Query()] = None,
):
    stmt = select(Finding).where(Finding.case_id == case.id).order_by(Finding.id.desc())
    if section_id is not None:
        section = get_in_case(session, CaseSection, case.id, section_id, "El inciso")
        if section.name.lower() == lens.UNCLASSIFIED:
            stmt = stmt.where(or_(Finding.section_id == section.id, Finding.section_id.is_(None)))
        else:
            stmt = stmt.where(Finding.section_id == section.id)
    elif unclassified:
        fallback = lens.unclassified_section(session, case.id)
        cond = Finding.section_id.is_(None)
        stmt = stmt.where(or_(cond, Finding.section_id == fallback.id) if fallback is not None else cond)
    if entity_id is not None:
        stmt = stmt.where(Finding.entity_id == entity_id)
    if kind:
        stmt = stmt.where(Finding.kind == kind.strip().lower())
    if since is not None:
        stmt = stmt.where(Finding.created_at >= since)
    items, total = paginate(session, stmt, page)
    return page_of(items, total, page)


@router.get("/findings/{finding_id}", response_model=FindingOut, summary="Ver hallazgo")
def get_finding(finding_id: int, case: ReadCase, session: DB):
    return get_in_case(session, Finding, case.id, finding_id, "El hallazgo")


@router.patch("/findings/{finding_id}", response_model=FindingOut, summary="Mover de inciso o editar la nota")
def update_finding(finding_id: int, body: FindingUpdate, case: WriteCase, session: DB, user: CurrentUser):
    finding = get_in_case(session, Finding, case.id, finding_id, "El hallazgo")
    data = body.model_dump(exclude_unset=True)
    changes = {}
    if "section_id" in data and data["section_id"] != finding.section_id:
        if data["section_id"] is not None:
            get_in_case(session, CaseSection, case.id, data["section_id"], "El inciso")
        changes["section_id"] = {"from": finding.section_id, "to": data["section_id"]}
        finding.section_id = data["section_id"]
    if data.get("note") is not None and data["note"] != finding.note:
        changes["note"] = {"from": finding.note, "to": data["note"]}
        finding.note = data["note"]
    if changes:
        auditlog.record(session, "finding.update", user_id=user.id, case_id=case.id,
                        target=f"finding:{finding.id}", detail={"changes": changes})
        session.commit()
    return finding


@router.delete("/findings/{finding_id}", status_code=204,
               summary="Quitar un hallazgo del expediente (su fuente y su entidad se conservan)")
def delete_finding(finding_id: int, case: WriteCase, session: DB, user: CurrentUser):
    finding = get_in_case(session, Finding, case.id, finding_id, "El hallazgo")
    detail = {"kind": finding.kind, "value": finding.value[:200], "source_id": finding.source_id,
              "entity_id": finding.entity_id, "section_id": finding.section_id}
    session.delete(finding)
    session.flush()
    auditlog.record(session, "finding.delete", user_id=user.id, case_id=case.id,
                    target=f"finding:{finding_id}", detail=detail)
    session.commit()
    return Response(status_code=204)
