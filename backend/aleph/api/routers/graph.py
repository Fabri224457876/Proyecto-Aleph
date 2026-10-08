"""Grafo del caso: entidades, relaciones, vistas para el visor, búsqueda, fusión y revisión."""

from typing import Annotated, Literal

from fastapi import HTTPException, Query, Response
from sqlalchemy import String, cast, or_, select

from aleph.core.models import Account, Entity, Post, Relation, Source
from aleph.core.schemas import ENTITY_TYPES

from .. import auditlog
from ..deps import DB, CurrentUser, PageParams, Paging, ReadCase, WriteCase, page_of, paginate
from ..presenters import accounts_out, graph_edge, graph_node, graph_out
from ..routing import make_router
from ..schemas import (
    EntityCreate,
    EntityOut,
    EntityUpdate,
    GraphOut,
    MergeIn,
    MergeOut,
    Page,
    PathOut,
    PostHit,
    PostOut,
    RelationCreate,
    RelationOut,
    RelationUpdate,
    ReviewIn,
    SearchOut,
)
from ..services import graph as graph_service
from ..services.util import get_in_case, like_pattern

router = make_router(prefix="/cases/{case_id}", tags=["grafo"])

Status = Literal["proposed", "confirmed", "rejected"]
StatusFilter = Annotated[
    list[Status] | None,
    Query(alias="status", description="Estados a incluir. Por defecto: proposed y confirmed"),
]
TypeFilter = Annotated[list[str] | None, Query(alias="type", description="Tipos de entidad a incluir")]


def _types(types: list[str] | None) -> list[str] | None:
    if not types:
        return None
    unknown = sorted(set(types) - set(ENTITY_TYPES))
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo de entidad inválido: {', '.join(unknown)}. Valores permitidos: {', '.join(ENTITY_TYPES)}.",
        )
    return types


def _check_source(session, case_id: int, source_id: int | None) -> None:
    if source_id is not None:
        get_in_case(session, Source, case_id, source_id, "La fuente")


# ---------------------------------------------------------------- entidades

@router.post("/entities", response_model=EntityOut, status_code=201, summary="Crear entidad")
def create_entity(body: EntityCreate, case: WriteCase, session: DB, user: CurrentUser):
    _check_source(session, case.id, body.source_id)
    entity = Entity(case_id=case.id, **body.model_dump())
    session.add(entity)
    session.flush()
    auditlog.record(
        session, "entity.create", user_id=user.id, case_id=case.id, target=f"entity:{entity.id}",
        detail={"type": entity.type, "label": entity.label, "status": entity.status, "source_id": entity.source_id},
    )
    session.commit()
    return entity


@router.get("/entities", response_model=Page[EntityOut], summary="Listar entidades")
def list_entities(
    case: ReadCase, session: DB, page: Paging, types: TypeFilter = None, statuses: StatusFilter = None,
    q: Annotated[str | None, Query(description="Texto en la etiqueta")] = None,
):
    stmt = select(Entity).where(Entity.case_id == case.id).order_by(Entity.id)
    if _types(types):
        stmt = stmt.where(Entity.type.in_(types))
    if statuses:
        stmt = stmt.where(Entity.status.in_(statuses))
    if q and q.strip():
        stmt = stmt.where(Entity.label.ilike(like_pattern(q.strip()), escape="\\"))
    items, total = paginate(session, stmt, page)
    return page_of(items, total, page)


@router.post("/entities/merge", response_model=MergeOut, summary="Fusionar dos entidades duplicadas")
def merge_entities(body: MergeIn, case: WriteCase, session: DB, user: CurrentUser):
    result = graph_service.merge_entities(session, case.id, body.keep_id, body.duplicate_id, user.id)
    session.commit()
    return MergeOut(
        entity=EntityOut.model_validate(result.entity), relations_repointed=result.relations_repointed,
        relations_removed=result.relations_removed, accounts_repointed=result.accounts_repointed,
    )


@router.get("/entities/{entity_id}", response_model=EntityOut, summary="Ver entidad")
def get_entity(entity_id: int, case: ReadCase, session: DB):
    return get_in_case(session, Entity, case.id, entity_id, "La entidad")


@router.patch("/entities/{entity_id}", response_model=EntityOut, summary="Editar entidad")
def update_entity(entity_id: int, body: EntityUpdate, case: WriteCase, session: DB, user: CurrentUser):
    entity = get_in_case(session, Entity, case.id, entity_id, "La entidad")
    data = body.model_dump(exclude_unset=True)
    if "source_id" in data:
        _check_source(session, case.id, data["source_id"])
    changes = {}
    for field, value in data.items():
        if (value is not None or field == "source_id") and getattr(entity, field) != value:
            changes[field] = {"from": getattr(entity, field), "to": value}
            setattr(entity, field, value)
    if changes:
        auditlog.record(session, "entity.update", user_id=user.id, case_id=case.id,
                        target=f"entity:{entity.id}", detail={"changes": changes})
        session.commit()
    return entity


@router.delete("/entities/{entity_id}", status_code=204, summary="Borrar entidad y sus relaciones")
def delete_entity(entity_id: int, case: WriteCase, session: DB, user: CurrentUser):
    graph_service.delete_entity(session, case.id, entity_id, user.id)
    session.commit()
    return Response(status_code=204)


@router.post("/entities/{entity_id}/review", response_model=EntityOut,
             summary="Aceptar o rechazar una entidad propuesta")
def review_entity(entity_id: int, body: ReviewIn, case: WriteCase, session: DB, user: CurrentUser):
    entity = graph_service.review_item(session, case.id, Entity, entity_id, body.decision, user.id, body.note)
    session.commit()
    return entity


@router.get("/entities/{entity_id}/neighbors", response_model=GraphOut, summary="Vecindario de un nodo")
def neighbors(
    entity_id: int, case: ReadCase, session: DB, user: CurrentUser,
    depth: Annotated[int, Query(ge=1, le=4, description="Saltos desde el nodo")] = 1,
    statuses: StatusFilter = None,
):
    sub = graph_service.neighborhood(
        session, case.id, entity_id, depth=depth, statuses=statuses or graph_service.VISIBLE
    )
    auditlog.record(session, "graph.neighbors", user_id=user.id, case_id=case.id,
                    target=f"entity:{entity_id}", detail={"depth": depth, "nodes": len(sub.entities)})
    session.commit()
    return graph_out(sub)


# ---------------------------------------------------------------- relaciones

@router.post("/relations", response_model=RelationOut, status_code=201, summary="Crear relación")
def create_relation(body: RelationCreate, case: WriteCase, session: DB, user: CurrentUser):
    get_in_case(session, Entity, case.id, body.src_id, "La entidad")
    get_in_case(session, Entity, case.id, body.dst_id, "La entidad")
    _check_source(session, case.id, body.source_id)
    relation = Relation(case_id=case.id, **body.model_dump())
    session.add(relation)
    session.flush()
    auditlog.record(
        session, "relation.create", user_id=user.id, case_id=case.id, target=f"relation:{relation.id}",
        detail={"type": relation.type, "src_id": relation.src_id, "dst_id": relation.dst_id,
                "status": relation.status, "source_id": relation.source_id},
    )
    session.commit()
    return relation


@router.get("/relations", response_model=Page[RelationOut], summary="Listar relaciones")
def list_relations(
    case: ReadCase, session: DB, page: Paging, statuses: StatusFilter = None,
    type: Annotated[str | None, Query(description="Tipo de relación exacto")] = None,
    entity_id: Annotated[int | None, Query(description="Relaciones que tocan esta entidad")] = None,
):
    stmt = select(Relation).where(Relation.case_id == case.id).order_by(Relation.id)
    if statuses:
        stmt = stmt.where(Relation.status.in_(statuses))
    if type:
        stmt = stmt.where(Relation.type == type)
    if entity_id is not None:
        stmt = stmt.where(or_(Relation.src_id == entity_id, Relation.dst_id == entity_id))
    items, total = paginate(session, stmt, page)
    return page_of(items, total, page)


@router.get("/relations/{relation_id}", response_model=RelationOut, summary="Ver relación")
def get_relation(relation_id: int, case: ReadCase, session: DB):
    return get_in_case(session, Relation, case.id, relation_id, "La relación")


@router.patch("/relations/{relation_id}", response_model=RelationOut, summary="Editar relación")
def update_relation(relation_id: int, body: RelationUpdate, case: WriteCase, session: DB, user: CurrentUser):
    relation = get_in_case(session, Relation, case.id, relation_id, "La relación")
    data = body.model_dump(exclude_unset=True)
    if "source_id" in data:
        _check_source(session, case.id, data["source_id"])
    changes = {}
    for field, value in data.items():
        if (value is not None or field == "source_id") and getattr(relation, field) != value:
            changes[field] = {"from": getattr(relation, field), "to": value}
            setattr(relation, field, value)
    if changes:
        auditlog.record(session, "relation.update", user_id=user.id, case_id=case.id,
                        target=f"relation:{relation.id}", detail={"changes": changes})
        session.commit()
    return relation


@router.delete("/relations/{relation_id}", status_code=204, summary="Borrar relación")
def delete_relation(relation_id: int, case: WriteCase, session: DB, user: CurrentUser):
    relation = get_in_case(session, Relation, case.id, relation_id, "La relación")
    detail = {"type": relation.type, "src_id": relation.src_id, "dst_id": relation.dst_id}
    session.delete(relation)
    session.flush()
    auditlog.record(session, "relation.delete", user_id=user.id, case_id=case.id,
                    target=f"relation:{relation_id}", detail=detail)
    session.commit()
    return Response(status_code=204)


@router.post("/relations/{relation_id}/review", response_model=RelationOut,
             summary="Aceptar o rechazar una relación propuesta")
def review_relation(relation_id: int, body: ReviewIn, case: WriteCase, session: DB, user: CurrentUser):
    relation = graph_service.review_item(session, case.id, Relation, relation_id, body.decision, user.id, body.note)
    session.commit()
    return relation


# ---------------------------------------------------------------- vistas del grafo

@router.get("/graph", response_model=GraphOut, summary="Grafo del caso, listo para el visor (queda auditado)")
def get_graph(
    case: ReadCase, session: DB, user: CurrentUser, types: TypeFilter = None, statuses: StatusFilter = None,
    min_confidence: Annotated[float, Query(ge=0, le=1)] = 0.0,
    include_isolated: Annotated[bool, Query(description="Incluir nodos sin aristas")] = True,
    limit: Annotated[int, Query(ge=1, le=10000, description="Máximo de nodos")] = 2000,
):
    """Nodos y aristas. Solo se devuelven aristas cuyos dos extremos están entre los nodos."""
    sub = graph_service.case_graph(
        session, case.id, types=_types(types), statuses=statuses or graph_service.VISIBLE,
        min_confidence=min_confidence, include_isolated=include_isolated, limit=limit,
    )
    auditlog.record(
        session, "graph.view", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
        detail={"nodes": len(sub.entities), "edges": len(sub.relations), "types": types or [],
                "statuses": list(statuses or graph_service.VISIBLE)},
    )
    session.commit()
    return graph_out(sub)


@router.get("/graph/path", response_model=PathOut, summary="Camino más corto entre dos nodos")
def get_path(
    case: ReadCase, session: DB, user: CurrentUser,
    source: Annotated[int, Query(description="Id de la entidad de origen")],
    target: Annotated[int, Query(description="Id de la entidad de destino")],
    statuses: StatusFilter = None,
):
    sub = graph_service.shortest_path(
        session, case.id, source, target, statuses=statuses or graph_service.VISIBLE
    )
    auditlog.record(
        session, "graph.path", user_id=user.id, case_id=case.id, target=f"entity:{source}",
        detail={"source": source, "target": target, "found": sub is not None},
    )
    session.commit()
    if sub is None:
        return PathOut(found=False)
    degrees = sub.degrees()
    return PathOut(
        found=True, length=len(sub.entities) - 1,
        nodes=[graph_node(e, degrees.get(e.id, 0)) for e in sub.entities],
        edges=[graph_edge(r) for r in sub.relations],
    )


@router.get("/search", response_model=SearchOut, summary="Buscar texto en entidades, cuentas y publicaciones")
def search(
    case: ReadCase, session: DB, user: CurrentUser,
    q: Annotated[str, Query(min_length=2, max_length=200, description="Texto a buscar")],
    limit: Annotated[int, Query(ge=1, le=200, description="Máximo por categoría")] = 25,
):
    text = q.strip()
    if len(text) < 2:
        raise HTTPException(status_code=422, detail="La búsqueda necesita al menos dos caracteres.")
    pattern = like_pattern(text)

    def like(column):
        return column.ilike(pattern, escape="\\")

    entity_stmt = select(Entity).where(
        Entity.case_id == case.id, or_(like(Entity.label), like(cast(Entity.props, String)))
    ).order_by(Entity.id)
    account_stmt = select(Account).where(
        Account.case_id == case.id,
        or_(like(Account.handle), like(Account.display_name), like(Account.bio), like(Account.platform_uid)),
    ).order_by(Account.id)
    post_stmt = (
        select(Post, Account).join(Account, Post.account_id == Account.id)
        .where(Account.case_id == case.id, like(Post.text))
        .order_by(Post.created_at.desc(), Post.id.desc())
    )

    top = PageParams(limit=limit, offset=0)
    entities, n_entities = paginate(session, entity_stmt, top)
    accounts, n_accounts = paginate(session, account_stmt, top)
    post_rows, n_posts = paginate(session, post_stmt, top, scalars=False)
    posts = [
        PostHit(**PostOut.model_validate(post).model_dump(), platform=account.platform, handle=account.handle)
        for post, account in post_rows
    ]
    totals = {"entities": n_entities, "accounts": n_accounts, "posts": n_posts}
    auditlog.record(session, "graph.search", user_id=user.id, case_id=case.id, target=f"case:{case.id}",
                    detail={"q": text, **totals})
    session.commit()
    return SearchOut(
        query=text, entities=[EntityOut.model_validate(e) for e in entities],
        accounts=accounts_out(session, accounts), posts=posts, totals=totals,
    )
