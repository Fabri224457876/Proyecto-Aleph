"""Trabajos, auditoría y catálogo de conectores."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import HTTPException, Query
from sqlalchemy import func, select

from aleph.core import audit as core_audit
from aleph.core.models import AuditEvent, Job

from .. import auditlog
from ..deps import DB, AnyUser, Auditor, Paging, SettingsDep, page_of, paginate
from ..routing import make_router
from ..schemas import AuditEventOut, AuditVerifyOut, ConnectorOut, JobOut, Message, Page
from ..services.util import as_utc

jobs = make_router(prefix="/jobs", tags=["trabajos"])
audit = make_router(prefix="/audit", tags=["auditoría"])
connectors = make_router(prefix="/connectors", tags=["conectores"])


@jobs.get("", response_model=Page[JobOut], summary="Listar trabajos")
def list_jobs(
    session: DB, user: AnyUser, page: Paging,
    case_id: Annotated[int | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
    status: Annotated[Literal["queued", "running", "done", "failed"] | None, Query()] = None,
):
    stmt = select(Job).order_by(Job.id.desc())
    if case_id is not None:
        stmt = stmt.where(Job.case_id == case_id)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    if status:
        stmt = stmt.where(Job.status == status)
    items, total = paginate(session, stmt, page)
    return page_of(items, total, page)


@jobs.get("/{job_id}", response_model=JobOut, summary="Consultar un trabajo")
def get_job(job_id: int, session: DB, user: AnyUser):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"El trabajo {job_id} no existe.")
    return job


@audit.get("/events", response_model=Page[AuditEventOut], summary="Eventos de auditoría (auditor o admin)")
def list_events(
    session: DB, user: Auditor, page: Paging,
    case_id: Annotated[int | None, Query()] = None,
    user_id: Annotated[int | None, Query()] = None,
    action: Annotated[str | None, Query(description="Acción exacta o prefijo terminado en punto, p. ej. `menard.`")] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
):
    stmt = select(AuditEvent)
    if case_id is not None:
        stmt = stmt.where(AuditEvent.case_id == case_id)
    if user_id is not None:
        stmt = stmt.where(AuditEvent.user_id == user_id)
    if action:
        if action.endswith("."):
            stmt = stmt.where(AuditEvent.action.startswith(action, autoescape=True))
        else:
            stmt = stmt.where(AuditEvent.action == action)
    # `ts` es texto ISO 8601 en UTC: el orden alfabético coincide con el cronológico
    if since is not None:
        stmt = stmt.where(AuditEvent.ts >= as_utc(since).isoformat())
    if until is not None:
        stmt = stmt.where(AuditEvent.ts <= as_utc(until).isoformat())
    stmt = stmt.order_by(AuditEvent.id.asc() if order == "asc" else AuditEvent.id.desc())
    items, total = paginate(session, stmt, page)
    items = [AuditEventOut.model_validate(e) for e in items]
    auditlog.record(session, "audit.view", user_id=user.id, target="audit",
                    detail={"case_id": case_id, "user_id": user_id, "action": action or "", "offset": page.offset})
    session.commit()
    return page_of(items, total, page)


@audit.get("/verify", response_model=AuditVerifyOut, summary="Verificar la integridad de la cadena de auditoría")
def verify(session: DB, user: Auditor):
    ok, broken = core_audit.verify_chain(session)
    total = session.scalar(select(func.count(AuditEvent.id))) or 0
    auditlog.record(session, "audit.verify", user_id=user.id, target="audit",
                    detail={"ok": ok, "broken_event_id": broken, "events": total})
    session.commit()
    detail = (
        "La cadena de auditoría está íntegra." if ok
        else f"La cadena está rota a partir del evento {broken}: fue alterado, o se borró uno anterior."
    )
    return AuditVerifyOut(ok=ok, broken_event_id=broken, total_events=total, detail=detail)


@connectors.get("", response_model=list[ConnectorOut], summary="Conectores instalados",
                responses={503: {"model": Message, "description": "El módulo de conectores no está instalado"}})
def list_connectors(user: AnyUser, settings: SettingsDep):
    try:
        import aleph.connectors  # noqa: F401  (al importarse registra los conectores)
        from aleph.connectors.base import list_connectors as registry
    except ImportError:
        raise HTTPException(status_code=503, detail="El módulo de conectores no está disponible.") from None
    return [
        ConnectorOut(
            name=c.name, title=getattr(c, "title", c.name), mode=getattr(c, "mode", "live"),
            params=dict(c.params), requires=list(c.requires),
            available=all(getattr(settings, key, "") for key in c.requires),
        )
        for c in registry()
    ]
