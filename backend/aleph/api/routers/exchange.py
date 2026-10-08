"""Intercambio CTI: exportar el caso a STIX 2.1 y a MISP, e importar un Bundle STIX como propuestas.

Exportar deja constancia en la auditoría (formato, TLP máximo, si se incluyeron pendientes y el hash
del archivo). Por defecto solo salen objetos confirmados por un analista; `include_pending=true`
incluye propuestas y hipótesis de MENARD pendientes, marcadas como tales.
"""

import hashlib
from typing import Annotated, Any, Literal

from fastapi import Body, Query, Response
from pydantic import BaseModel, Field

from .. import auditlog
from ..deps import DB, CurrentUser, ReadCase, SettingsDep, WriteCase
from ..errors import Conflict, Invalid
from ..routing import make_router
from ..schemas import Message
from ..services import cti as cti_service
from ..services.cti import build_cti_case

router = make_router(prefix="/cases/{case_id}", tags=["intercambio CTI"])

TlpMax = Annotated[
    Literal["clear", "green", "amber", "amber+strict", "red"] | None,
    Query(description="Nivel TLP máximo: no se exporta nada con un TLP más restrictivo"),
]
IncludePending = Annotated[bool, Query(description="Incluir propuestas e hipótesis de MENARD pendientes")]
_BUNDLE_RESPONSES = {
    409: {"model": Message, "description": "No hay objetos exportables con esos criterios"},
}


def _file_response(body: str, *, filename: str, sha: str, extra: dict[str, str] | None = None) -> Response:
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "X-Aleph-SHA256": sha}
    headers.update(extra or {})
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/export/stix", summary="Exportar el caso como Bundle STIX 2.1 (queda auditado)",
            responses=_BUNDLE_RESPONSES, response_class=Response)
def export_stix(case: ReadCase, session: DB, user: CurrentUser, max_tlp: TlpMax = None,
                include_pending: IncludePending = False):
    """Bundle determinista: exportar dos veces el mismo caso da el mismo JSON.

    Si hay objetos `x-aleph-*` (billeteras, eventos, lugares sin coordenadas), el bundle exige
    `allow_custom` para leer sus referencias: el header `X-Aleph-Requires-Allow-Custom` lo indica.
    """
    cti_case = build_cti_case(session, case, include_pending=include_pending)
    export = cti_service.export_stix(cti_case, max_tlp)
    if export.bundle is None:
        raise Conflict("No hay objetos exportables con los criterios indicados.")
    body = export.to_json()
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    auditlog.record(
        session, "case.export", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
        detail={"format": "stix-2.1", "max_tlp": max_tlp or "", "include_pending": include_pending,
                "objects": len(export.bundle.objects), "skipped": len(export.skipped), "sha256": sha},
    )
    session.commit()
    return _file_response(
        body, filename=f"aleph-caso-{case.id}.stix.json", sha=sha,
        extra={"X-Aleph-Requires-Allow-Custom": "true" if export.requires_allow_custom else "false"},
    )


@router.get("/export/misp", summary="Exportar el caso como evento MISP (queda auditado)",
            response_class=Response)
def export_misp(case: ReadCase, session: DB, user: CurrentUser, max_tlp: TlpMax = None,
                include_pending: IncludePending = False):
    """Evento MISP con atributos. Las relaciones entre entidades no se exportan en esta versión
    (aparecen como `skipped` en la auditoría)."""
    cti_case = build_cti_case(session, case, include_pending=include_pending)
    export = cti_service.export_misp_event(cti_case, max_tlp)
    body = export.to_json()
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    auditlog.record(
        session, "case.export", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
        detail={"format": "misp-event", "max_tlp": max_tlp or "", "include_pending": include_pending,
                "attributes": len(export.document["Event"]["Attribute"]), "skipped": len(export.skipped),
                "sha256": sha},
    )
    session.commit()
    return _file_response(body, filename=f"aleph-caso-{case.id}.misp.json", sha=sha)


class StixImportOut(BaseModel):
    source_id: int
    entidades_creadas: int
    entidades_existentes: int
    relaciones_creadas: int
    relaciones_existentes: int
    tecnicas_propuestas: int
    tecnicas_ya_conocidas: int
    caso_del_informe: dict | None = Field(default=None, description="Metadatos del objeto report, si venía")
    omitidos: list[str] = Field(default_factory=list, description="Objetos que no tienen equivalente (hasta 50)")
    omitidos_total: int = 0
    avisos: list[str] = Field(default_factory=list)


@router.post(
    "/import/stix",
    response_model=StixImportOut,
    status_code=201,
    summary="Importar un Bundle STIX 2.1 como entidades y relaciones propuestas",
    responses={400: {"model": Message, "description": "El documento no es un bundle STIX válido"}},
)
def import_stix(case: WriteCase, session: DB, user: CurrentUser, settings: SettingsDep,
                bundle: Annotated[dict[str, Any], Body(description="Bundle STIX 2.1 en JSON")]) -> StixImportOut:
    if bundle.get("type") != "bundle":
        raise Invalid("El documento no es un bundle STIX (le falta \"type\": \"bundle\").")
    result = cti_service.import_bundle_to_graph(session, case, bundle, user.id, settings.data_dir)
    summary = result["summary"]
    auditlog.record(
        session, "stix.import", user_id=user.id, case_id=case.id, target=f"source:{result['source_id']}",
        detail={"entities_created": summary.entities_created, "relations_created": summary.relations_created,
                "techniques_proposed": result["techniques_proposed"], "skipped": result["skipped_total"]},
    )
    session.commit()
    return StixImportOut(
        source_id=result["source_id"],
        entidades_creadas=summary.entities_created, entidades_existentes=summary.entities_existing,
        relaciones_creadas=summary.relations_created, relaciones_existentes=summary.relations_existing,
        tecnicas_propuestas=result["techniques_proposed"], tecnicas_ya_conocidas=result["techniques_known"],
        caso_del_informe=result["case_meta"], omitidos=result["skipped"], omitidos_total=result["skipped_total"],
        avisos=summary.warnings[:50],
    )
