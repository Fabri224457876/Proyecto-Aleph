"""Búsqueda: dentro de Aleph (texto y significado), preguntas al caso y plan de búsqueda OSINT externa.

- `GET /search`: entidades, cuentas, publicaciones, fuentes, hallazgos, hipótesis de MENARD y
  casos que el usuario ve. Con `semantic=true` reordena con embeddings locales.
- `POST /cases/{id}/ask`: recupera los fragmentos más relevantes del caso y FUNES responde
  citándolos. Las citas a ids que no estaban en el contexto se marcan con `?`.
- `GET /cases/{id}/lookup`: reconoce el dato y lista las fuentes públicas que aplican.

Las búsquedas y las preguntas quedan en la auditoría (qué se buscó y quién), como el resto.
"""

import asyncio
import hashlib
from typing import Annotated, Literal

import httpx
from fastapi import Depends, Query
from pydantic import BaseModel, Field

from aleph.funes.client import LLMError
from aleph.funes.reasoning import Fragment, answer_question

from .. import auditlog
from ..deps import DB, AnyUser, ReadCase, SettingsDep, readable_case
from ..errors import EngineUnavailable
from ..routing import make_router
from ..services import lookup as lookup_service
from ..services import search as search_service
from ..services.funes import NO_RESPONSE, LlmProbe, make_client
from ..services.outbound import llm_http

router = make_router(tags=["búsqueda"])
LlmDep = Annotated[httpx.AsyncClient | None, Depends(llm_http)]
Kind = Literal["case", "entity", "account", "post", "source", "finding", "link"]


class HitOut(BaseModel):
    ref: str = Field(description="Id corto para citar: E entidad, A cuenta, P publicación, S fuente, F hallazgo, "
                                 "H hipótesis de MENARD, K caso")
    kind: str
    id: int
    case_id: int
    title: str
    snippet: str
    score: float
    lexical: float
    semantic: float | None = None
    status: str = ""
    extra: dict = Field(default_factory=dict)


class GlobalSearchOut(BaseModel):
    query: str
    semantic_status: str = Field(description="usado | no_configurado | no_responde | omitido")
    warnings: list[str] = Field(default_factory=list)
    items: list[HitOut]


def _out(h: search_service.Hit) -> HitOut:
    return HitOut(ref=h.ref, kind=h.kind, id=h.id, case_id=h.case_id, title=h.title, snippet=h.snippet,
                  score=round(h.score, 4), lexical=round(h.lexical, 4), semantic=h.semantic, status=h.status,
                  extra=h.extra)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@router.get("/search", response_model=GlobalSearchOut, summary="Buscar en los casos que ves (texto y significado)")
def search(
    session: DB, user: AnyUser, settings: SettingsDep, llm: LlmDep,
    q: Annotated[str, Query(min_length=2, max_length=300, description="Qué buscar")],
    case_id: Annotated[int | None, Query(description="Solo en este caso")] = None,
    kind: Annotated[list[Kind] | None, Query(description="Tipos a incluir (por defecto, todos)")] = None,
    semantic: Annotated[bool, Query(description="Reordenar por significado con embeddings locales")] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> GlobalSearchOut:
    if case_id is not None:
        readable_case(case_id, session, user)  # 404 si no existe o no lo ve
    kinds = tuple(kind) if kind else search_service.KINDS
    hits, status, warnings = search_service.search(session, user, q, settings=settings, http=llm, case_id=case_id,
                                                   kinds=kinds, semantic=semantic, limit=limit)
    auditlog.record(session, "search.run", user_id=user.id, case_id=case_id, target=f"case:{case_id}" if case_id else "",
                    detail={"q": q[:200], "kinds": list(kinds), "semantic": status, "results": len(hits)})
    session.commit()
    return GlobalSearchOut(query=q, semantic_status=status, warnings=warnings, items=[_out(h) for h in hits])


ASK_BOOST = {"entity": 1.3, "finding": 1.3, "link": 1.2, "source": 1.1}


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    semantic: bool = Field(default=True, description="Recuperar también por significado")
    k: int = Field(default=12, ge=3, le=30, description="Fragmentos que se le pasan al modelo")


class AskOut(BaseModel):
    answer: str
    llm_status: str
    retrieval: str = Field(description="Cómo se recuperó: semántica usada o solo texto")
    citations: list[HitOut] = Field(description="Fragmentos citados en la respuesta")
    context: list[HitOut] = Field(description="Todos los fragmentos que vio el modelo")
    invalid_citations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@router.post(
    "/cases/{case_id}/ask", response_model=AskOut,
    summary="Preguntarle al caso: FUNES responde con los datos del caso y los cita",
    responses={503: {"description": "El LLM no está configurado o no responde"}},
)
def ask(body: AskIn, case: ReadCase, session: DB, user: AnyUser, settings: SettingsDep, llm: LlmDep) -> AskOut:
    # Para responder pesan más los datos estructurados (entidades, hallazgos, hipótesis) que las publicaciones.
    hits, status, warnings = search_service.search(session, user, body.question, settings=settings, http=llm,
                                                   case_id=case.id, semantic=body.semantic, limit=body.k,
                                                   boost=ASK_BOOST)
    if not hits and status != "usado":
        # Sin coincidencias por texto: al menos lo más reciente del caso, para que el modelo diga qué hay.
        hits = search_service.case_pool(session, case.id, limit=4)[: body.k]
    fragments = [Fragment(id=h.ref, kind=h.kind, title=h.title, text=h.text or h.snippet) for h in hits]

    client = make_client(settings, llm)
    probe = LlmProbe(client)

    async def run():
        try:
            return await answer_question(probe, body.question, fragments)  # type: ignore[arg-type]
        finally:
            await client.aclose()

    try:
        result = asyncio.run(run())
    except LLMError:
        result = None
    if result is None or probe.status == NO_RESPONSE:
        raise EngineUnavailable(
            f"El LLM de FUNES no respondió en {settings.llm_base_url}: {probe.last_failure[:300]}. "
            "Revisá que el servidor esté levantado y que ALEPH_LLM_BASE_URL apunte a él.")
    by_ref = {h.ref: h for h in hits}
    auditlog.record(session, "funes.ask", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
                    detail={"question_sha256": _digest(body.question), "chars": len(body.question),
                            "context": [h.ref for h in hits], "cited": result.cited, "invalid": result.invalid,
                            "retrieval": status})
    session.commit()
    if result.invalid:
        warnings.append(f"La respuesta citó {len(result.invalid)} id(s) que no estaban en el contexto; "
                        "quedan marcados con «?».")
    return AskOut(answer=result.answer, llm_status=probe.status, retrieval=status,
                  citations=[_out(by_ref[r]) for r in result.cited if r in by_ref],
                  context=[_out(h) for h in hits], invalid_citations=result.invalid, warnings=warnings)


@router.get("/cases/{case_id}/lookup", response_model=lookup_service.LookupPlan,
            summary="Búsqueda OSINT externa: qué fuentes públicas aplican a un dato")
def lookup(case: ReadCase, user: AnyUser, settings: SettingsDep,
           q: Annotated[str, Query(min_length=1, max_length=2048, description="Usuario, correo, dominio, URL, IP…")]
           ) -> lookup_service.LookupPlan:
    return lookup_service.plan(case.id, q, settings)
