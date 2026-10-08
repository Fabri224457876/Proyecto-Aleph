"""Conversión de modelos de la base a modelos de salida."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aleph.core.models import Account, AccountLink, Entity, Post, Relation, Source

from .schemas import (
    AccountBrief,
    AccountOut,
    GraphEdge,
    GraphNode,
    GraphOut,
    LinkOut,
    SourceOut,
)
from .services.graph import Subgraph


def source_out(source: Source) -> SourceOut:
    out = SourceOut.model_validate(source)
    out.has_raw = bool(source.raw_path)
    out.admiralty = f"{source.reliability}{source.credibility}"
    return out


def graph_node(entity: Entity, degree: int = 0) -> GraphNode:
    node = GraphNode.model_validate(entity)
    node.degree = degree
    return node


def graph_edge(relation: Relation) -> GraphEdge:
    return GraphEdge(
        id=relation.id, source=relation.src_id, target=relation.dst_id, type=relation.type,
        status=relation.status, confidence=relation.confidence, props=relation.props or {},
        source_id=relation.source_id,
    )


def graph_out(sub: Subgraph) -> GraphOut:
    degrees = sub.degrees()
    return GraphOut(
        nodes=[graph_node(e, degrees.get(e.id, 0)) for e in sub.entities],
        edges=[graph_edge(r) for r in sub.relations],
        counts={"nodes": len(sub.entities), "edges": len(sub.relations)},
        truncated=sub.truncated,
    )


def post_counts(session: Session, account_ids: list[int]) -> dict[int, int]:
    if not account_ids:
        return {}
    rows = session.execute(
        select(Post.account_id, func.count(Post.id)).where(Post.account_id.in_(account_ids)).group_by(Post.account_id)
    )
    return {account_id: count for account_id, count in rows}


def accounts_out(session: Session, accounts: list[Account]) -> list[AccountOut]:
    counts = post_counts(session, [a.id for a in accounts])
    result = []
    for account in accounts:
        out = AccountOut.model_validate(account)
        out.post_count = counts.get(account.id, 0)
        result.append(out)
    return result


def links_out(session: Session, links: list[AccountLink]) -> list[LinkOut]:
    ids = {link.account_a_id for link in links} | {link.account_b_id for link in links}
    accounts = {}
    if ids:
        accounts = {a.id: a for a in session.execute(select(Account).where(Account.id.in_(ids))).scalars()}
    result = []
    for link in links:
        signals = link.signals or {}
        result.append(LinkOut(
            id=link.id, case_id=link.case_id,
            account_a=AccountBrief.model_validate(accounts[link.account_a_id]),
            account_b=AccountBrief.model_validate(accounts[link.account_b_id]),
            score=link.score, confidence=str(signals.get("confidence", "")),
            summary=str(signals.get("summary", "")), signals=signals,
            review_status=link.review_status, reviewed_by=link.reviewed_by,
            review_note=link.review_note, created_at=link.created_at,
        ))
    return result
