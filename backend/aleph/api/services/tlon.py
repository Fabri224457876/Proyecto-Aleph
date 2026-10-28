"""TLÖN en la API: arma el grafo del caso, entrena, pide la revisión de FUNES y guarda propuestas.

Las hipótesis que FUNES no descarta entran al grafo como relaciones *propuestas* (aparecen en
Revisar); las descartadas quedan solo en el resultado del trabajo, para que se vea qué se filtró.
Si el LLM no está o no responde, TLÖN igual funciona: las hipótesis entran sin revisión y con aviso.
"""

import asyncio
from collections import Counter
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from aleph.core.models import Account, Entity, Relation
from aleph.funes.client import LLMError
from aleph.funes.reasoning import InventoryItem, Review, Statement, review_hypotheses
from aleph.funes.structured import StructuredOutputError
from aleph.tlon import TlonEdge, TlonInput, TlonNode, propose
from aleph.tlon.features import node_text
from aleph.tlon.schemas import Hypothesis, TlonParams, TlonReport

from ..errors import EngineUnavailable
from . import search as search_service
from .funes import NO_CONFIG, NO_RESPONSE, SKIPPED, USED, LlmProbe, make_client
from .proposals import canonical_json, make_source

INVENTORY_ENTITIES = 60
INVENTORY_RELATIONS = 100


class TlonRunIn(BaseModel):
    use_llm: bool = Field(default=True, description="Que FUNES revise y explique las hipótesis")
    use_embeddings: bool = Field(default=True, description="Describir las entidades con embeddings locales")
    include_proposed: bool = Field(default=True, description="Aprender también de lo propuesto (con menos peso)")
    max_hypotheses: int = Field(default=12, ge=1, le=100)
    min_percentile: float = Field(default=0.5, ge=0, le=1)
    epochs: int = Field(default=300, ge=10, le=3000)


class HypothesisOut(Hypothesis):
    ref: str
    relation_id: int | None = Field(default=None, description="Relación propuesta creada (None si se descartó)")
    review: Review | None = None


class TlonRunOut(BaseModel):
    status: str
    message: str = ""
    nodes: int
    edges: int
    quality: dict | None = None
    feature_source: str = ""
    llm_status: str
    embeddings_status: str
    hypotheses: list[HypothesisOut] = Field(default_factory=list)
    lectura: list[Statement] = Field(default_factory=list)
    created_relations: int = 0
    source_id: int | None = None
    warnings: list[str] = Field(default_factory=list)


def build_input(session: Session, case_id: int, *, include_proposed: bool) -> TlonInput:
    statuses = ("confirmed", "proposed") if include_proposed else ("confirmed",)
    entities = session.execute(select(Entity).where(Entity.case_id == case_id, Entity.status.in_(statuses))
                               .order_by(Entity.id)).scalars().all()
    bios = {a.entity_id: f"{a.display_name}. {a.bio}" for a in session.execute(
        select(Account).where(Account.case_id == case_id, Account.entity_id.is_not(None))).scalars()}
    nodes = [TlonNode(id=e.id, type=e.type, label=e.label, props=e.props or {}, text=bios.get(e.id, ""),
                      confirmed=e.status == "confirmed") for e in entities]
    ids = {n.id for n in nodes}
    relations = session.execute(select(Relation).where(Relation.case_id == case_id, Relation.status.in_(statuses))
                                .order_by(Relation.id)).scalars().all()
    edges = [TlonEdge(src=r.src_id, dst=r.dst_id, type=r.type, confirmed=r.status == "confirmed")
             for r in relations if r.src_id in ids and r.dst_id in ids]
    rejected = session.execute(select(Relation.src_id, Relation.dst_id).where(
        Relation.case_id == case_id, Relation.status == "rejected")).all()
    return TlonInput(nodes=nodes, edges=edges, blocked=[(a, b) for a, b in rejected])


def _embed_nodes(data: TlonInput, settings, http) -> tuple[str, list[str]]:
    client = search_service.embed_client(settings, http)
    if client is None:
        return NO_CONFIG, ["Sin embeddings configurados: TLÖN describe las entidades con n-gramas de caracteres."]

    async def run():
        try:
            return await search_service._embed(client, [node_text(n) for n in data.nodes])
        finally:
            await client.aclose()

    try:
        vectors = asyncio.run(run())
    except LLMError as exc:
        return NO_RESPONSE, [f"El servidor de embeddings no respondió ({str(exc)[:160]}); se usaron n-gramas."]
    data.vectors = {n.id: v.tolist() for n, v in zip(data.nodes, vectors)}
    return USED, []


def _inventory(session: Session, case_id: int, data: TlonInput,
               hypotheses: list[Hypothesis]) -> tuple[list[InventoryItem], dict[str, Hypothesis]]:
    degree = Counter()
    for e in data.edges:
        degree[e.src] += 1
        degree[e.dst] += 1
    involved = {h.src for h in hypotheses} | {h.dst for h in hypotheses}
    ranked = sorted(data.nodes, key=lambda n: (n.id not in involved, -degree[n.id], n.id))
    chosen = {n.id: n for n in ranked[:INVENTORY_ENTITIES]}
    items = [InventoryItem(id=f"E{n.id}", text=f"{n.type}: {n.label}"
                           + (f" — {n.text[:160]}" if n.text.strip(". ") else "")
                           + ("" if n.confirmed else " (propuesta, sin confirmar)")) for n in chosen.values()]
    rels = session.execute(select(Relation).where(Relation.case_id == case_id, Relation.status != "rejected",
                                                  Relation.src_id.in_(chosen), Relation.dst_id.in_(chosen))
                           .order_by(Relation.id).limit(INVENTORY_RELATIONS)).scalars().all()
    items += [InventoryItem(id=f"R{r.id}", text=f"E{r.src_id} -[{r.type}]-> E{r.dst_id}"
                            + ("" if r.status == "confirmed" else " (propuesta)")) for r in rels]
    by_ref = {}
    for i, h in enumerate(hypotheses, 1):
        ref = f"T{i}"
        by_ref[ref] = h
        items.append(InventoryItem(id=ref, text=f"HIPÓTESIS DE TLÖN: E{h.src} -[{h.type}]-> E{h.dst}. "
                                                f"{h.evidence.explanation}"))
    return items, by_ref


def run(session: Session, case, user_id: int, body: TlonRunIn, *, settings, http) -> TlonRunOut:
    data = build_input(session, case.id, include_proposed=body.include_proposed)
    warnings: list[str] = []
    emb_status = SKIPPED
    if body.use_embeddings and data.nodes:
        emb_status, w = _embed_nodes(data, settings, http)
        warnings += w
    report: TlonReport = propose(data, TlonParams(max_hypotheses=body.max_hypotheses,
                                                  min_percentile=body.min_percentile, epochs=body.epochs))
    out = TlonRunOut(status=report.status, message=report.message, nodes=report.nodes, edges=report.edges,
                     quality=report.quality.model_dump() if report.quality else None,
                     feature_source=report.quality.feature_source if report.quality else "",
                     llm_status=SKIPPED, embeddings_status=emb_status, warnings=warnings)
    if report.status != "ok":
        return out

    items, by_ref = _inventory(session, case.id, data, report.hypotheses)
    reviews: dict[str, Review] = {}
    if body.use_llm and report.hypotheses:
        try:
            client = make_client(settings, http)
        except EngineUnavailable as exc:
            out.llm_status = NO_CONFIG
            warnings.append(f"{exc.message} Las hipótesis quedan sin revisión del modelo de lenguaje.")
        else:
            probe = LlmProbe(client)

            async def review():
                try:
                    return await review_hypotheses(probe, case.name, items, set(by_ref))  # type: ignore[arg-type]
                finally:
                    await client.aclose()

            try:
                result, w = asyncio.run(review())
                warnings += w
                reviews = {r.id: r for r in result.revisiones}
                out.lectura = result.lectura
            except (LLMError, StructuredOutputError) as exc:
                warnings.append(f"FUNES no pudo revisar las hipótesis ({str(exc)[:160]}); quedan sin revisión.")
            out.llm_status = probe.status

    payload = {"tlon": report.model_dump(), "revisiones": {k: v.model_dump() for k, v in reviews.items()},
               "lectura": [s.model_dump() for s in out.lectura]}
    source = make_source(session, case.id, user_id, kind="tlon", connector="tlon",
                         reference=f"TLÖN · modelo del caso · {datetime.now(UTC):%d/%m/%Y %H:%M} UTC",
                         payload=canonical_json(payload), data_dir=settings.data_dir)
    out.source_id = source.id
    for ref, h in by_ref.items():
        rv = reviews.get(ref)
        relation_id = None
        if rv is None or rv.veredicto != "descartable":
            # Una hipótesis nunca es certeza: la confianza va de 0,3 a 0,9 según la fuerza del patrón.
            rel = Relation(case_id=case.id, src_id=h.src, dst_id=h.dst, type=h.type[:64], status="proposed",
                           confidence=round(0.3 + 0.6 * h.percentile, 3), source_id=source.id,
                           props={"motor": "tlon", "puntaje": h.score, "percentil": h.percentile,
                                  "explicacion": h.evidence.explanation[:1000],
                                  "vecinos_en_comun": h.evidence.common_neighbors,
                                  **({"funes": rv.model_dump()} if rv else {})})
            session.add(rel)
            session.flush()
            relation_id = rel.id
            out.created_relations += 1
        out.hypotheses.append(HypothesisOut(**h.model_dump(), ref=ref, relation_id=relation_id, review=rv))
    out.warnings = warnings
    return out
