"""Persistencia idempotente de un `CollectionResult`. Lo usan la API y los workers."""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aleph.core.config import get_settings
from aleph.core.models import Account, Case, Entity, Post, Relation, Source
from aleph.core.schemas import ENTITY_TYPES, AccountRecord, CollectionResult, PostRecord

from .. import auditlog
from ..errors import Conflict, Invalid, NotFound
from .util import as_utc, clamp01

# Datos del AccountRecord que no tienen columna propia se guardan en Account.meta bajo esta clave
EXTRA_KEY = "aleph"
_EXTRA_FIELDS = ("avatar_url", "following_handles", "follower_handles")
_POST_FIELDS = ("text", "lang", "kind", "reply_to", "mentions", "hashtags", "urls", "client", "meta")


class IngestSummary(BaseModel):
    case_id: int
    source_id: int
    source_created: bool
    sha256: str
    accounts_created: int = 0
    accounts_updated: int = 0
    posts_created: int = 0
    posts_updated: int = 0
    posts_unchanged: int = 0
    entities_created: int = 0
    entities_existing: int = 0
    relations_created: int = 0
    relations_existing: int = 0
    account_ids: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def account_entity_label(platform: str, handle: str) -> str:
    return f"{platform}:{handle}"


def ensure_account_entity(session: Session, account: Account, source_id: int | None = None) -> Entity:
    """Toda cuenta tiene una entidad `account` en el grafo; la crea si falta."""
    if account.entity_id is not None:
        entity = session.get(Entity, account.entity_id)
        if entity is not None:
            return entity
    label = account_entity_label(account.platform, account.handle)
    # Si un analista ya había creado a mano la entidad de esta cuenta, se adopta en vez de duplicarla
    taken = select(Account.entity_id).where(Account.case_id == account.case_id, Account.entity_id.is_not(None))
    entity = session.execute(
        select(Entity).where(
            Entity.case_id == account.case_id, Entity.type == "account",
            func.lower(Entity.label) == label.lower(), Entity.id.not_in(taken),
        ).order_by(Entity.id)
    ).scalars().first()
    if entity is not None:
        account.entity_id = entity.id
        session.flush()
        return entity
    entity = Entity(
        case_id=account.case_id, type="account", label=label,
        props={"platform": account.platform, "handle": account.handle,
               "display_name": account.display_name, "url": account.url},
        confidence=1.0, status="confirmed", source_id=source_id or account.source_id,
    )
    session.add(entity)
    session.flush()
    account.entity_id = entity.id
    return entity


def account_to_record(account: Account) -> AccountRecord:
    meta = dict(account.meta or {})
    extra = meta.pop(EXTRA_KEY, None) or {}
    return AccountRecord(
        platform=account.platform, handle=account.handle, platform_uid=account.platform_uid,
        display_name=account.display_name, bio=account.bio, url=account.url,
        created_at_platform=as_utc(account.created_at_platform),
        followers=account.followers, following=account.following,
        avatar_url=extra.get("avatar_url", ""), avatar_phash=account.avatar_phash,
        following_handles=list(extra.get("following_handles", [])),
        follower_handles=list(extra.get("follower_handles", [])),
        meta=meta,
    )


def post_to_record(post: Post) -> PostRecord:
    return PostRecord(
        platform_post_id=post.platform_post_id, text=post.text, created_at=as_utc(post.created_at),
        lang=post.lang, kind=post.kind, reply_to=post.reply_to, mentions=list(post.mentions or []),
        hashtags=list(post.hashtags or []), urls=list(post.urls or []), client=post.client,
        meta=dict(post.meta or {}),
    )


def _canonical_bytes(result: CollectionResult) -> bytes:
    """Bytes que se guardan como evidencia: la respuesta cruda o, si no vino, el contenido normalizado."""
    if result.raw is not None:
        payload = result.raw
    else:
        payload = result.model_dump(mode="json", exclude={"raw", "retrieved_at", "warnings"})
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")


def _upsert_account(session: Session, case_id: int, rec: AccountRecord, source_id: int) -> tuple[Account, bool]:
    platform = rec.platform.strip().lower()
    handle = rec.handle.strip().lstrip("@")
    account = session.execute(
        select(Account).where(
            Account.case_id == case_id, Account.platform == platform,
            func.lower(Account.handle) == handle.lower(),
        )
    ).scalars().first()
    created = account is None
    if created:
        account = Account(case_id=case_id, platform=platform, handle=handle, source_id=source_id, meta={})
        session.add(account)
    # Un dato vacío en una recolección posterior no pisa lo que ya se sabía
    for field in ("platform_uid", "display_name", "bio", "url", "avatar_phash"):
        value = getattr(rec, field)
        if value:
            setattr(account, field, value)
    for field in ("followers", "following"):
        value = getattr(rec, field)
        if value is not None:
            setattr(account, field, value)
    if rec.created_at_platform is not None:
        account.created_at_platform = as_utc(rec.created_at_platform)
    meta = dict(account.meta or {})
    extra = dict(meta.get(EXTRA_KEY) or {})
    meta.update(rec.meta)
    for field in _EXTRA_FIELDS:
        value = getattr(rec, field)
        if value:
            extra[field] = value
    if extra:
        meta[EXTRA_KEY] = extra
    if meta != (account.meta or {}):
        account.meta = meta
    session.flush()
    ensure_account_entity(session, account, source_id)
    return account, created


def _upsert_posts(session: Session, account: Account, posts: list[PostRecord], summary: IngestSummary) -> None:
    existing = {
        p.platform_post_id: p
        for p in session.execute(select(Post).where(Post.account_id == account.id)).scalars()
    }
    for rec in posts:
        pid = rec.platform_post_id.strip()
        if not pid:
            summary.warnings.append(f"Publicación sin identificador en {account.platform}:{account.handle}; se omitió.")
            continue
        values = {f: getattr(rec, f) for f in _POST_FIELDS}
        created_at = as_utc(rec.created_at)
        post = existing.get(pid)
        if post is None:
            post = Post(account_id=account.id, platform_post_id=pid, created_at=created_at, **values)
            session.add(post)
            existing[pid] = post
            summary.posts_created += 1
            continue
        changed = False
        for field, value in values.items():
            if getattr(post, field) != value:
                setattr(post, field, value)
                changed = True
        if created_at is not None and as_utc(post.created_at) != created_at:
            post.created_at = created_at
            changed = True
        if changed:
            summary.posts_updated += 1
        else:
            summary.posts_unchanged += 1


def ingest_collection(
    session: Session,
    case_id: int,
    result: CollectionResult,
    user_id: int | None = None,
    *,
    data_dir: str | Path | None = None,
    store_raw: bool = True,
    entity_status: str = "confirmed",
) -> IngestSummary:
    """Persiste un `CollectionResult` en el caso. Idempotente: repetirlo no duplica nada.

    - Cuentas: upsert por (caso, plataforma, handle sin distinguir mayúsculas).
    - Publicaciones: upsert por (cuenta, id de la plataforma).
    - Cada cuenta queda con su entidad `account` en el grafo.
    - `Source`: una por (conector, referencia, sha256 del crudo); el crudo se guarda en `data_dir`.
    - Entidades: se reutilizan por (tipo, etiqueta); relaciones por (origen, destino, tipo).
      Un `RelationRecord` referencia entidades por su `ref`, por `"tipo:etiqueta"`, o una
      cuenta recolectada por su `AccountProfile.key` (`"plataforma:handle"`).
    - `entity_status="proposed"` deja entidades y relaciones nuevas para revisión humana.

    Registra el evento `collection.ingest` en la auditoría. No hace commit: lo hace el llamador.
    """
    if entity_status not in ("confirmed", "proposed"):
        raise Invalid("entity_status debe ser 'confirmed' o 'proposed'.")
    auditlog.guard(session)
    case = session.get(Case, case_id)
    if case is None:
        raise NotFound(f"El caso {case_id} no existe.")
    if case.status != "open":
        raise Conflict("El caso no está abierto: no admite nuevas recolecciones.")

    raw_bytes = _canonical_bytes(result)
    sha = hashlib.sha256(raw_bytes).hexdigest()
    source = session.execute(
        select(Source).where(
            Source.case_id == case_id, Source.kind == "connector",
            Source.connector == result.connector, Source.reference == result.reference,
            Source.sha256 == sha,
        )
    ).scalars().first()
    source_created = source is None
    if source_created:
        raw_path = ""
        if store_raw:
            base = Path(data_dir if data_dir is not None else get_settings().data_dir)
            rel = Path("cases") / str(case_id) / "raw" / f"{sha}.json"
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(raw_bytes)
            raw_path = rel.as_posix()
        source = Source(
            case_id=case_id, kind="connector", connector=result.connector, reference=result.reference,
            sha256=sha, raw_path=raw_path, retrieved_at=as_utc(result.retrieved_at), created_by=user_id,
        )
        session.add(source)
        session.flush()

    summary = IngestSummary(
        case_id=case_id, source_id=source.id, source_created=source_created, sha256=sha,
        warnings=list(result.warnings),
    )
    refs: dict[str, int] = {}

    def norm_key(record: AccountRecord) -> str:
        return f"{record.platform.strip().lower()}:{record.handle.strip().lstrip('@').lower()}"

    merged: dict[str, tuple[AccountRecord, list[PostRecord]]] = {}
    aliases: dict[str, str] = {}  # clave tal como la arma el conector -> clave normalizada
    for profile in result.profiles:
        if not profile.account.platform.strip() or not profile.account.handle.strip().lstrip("@"):
            summary.warnings.append("Cuenta sin plataforma o sin handle; se omitió.")
            continue
        key = norm_key(profile.account)
        aliases[profile.key] = key
        if key in merged:
            merged[key][1].extend(profile.posts)
        else:
            merged[key] = (profile.account, list(profile.posts))
    for key, (record, posts) in merged.items():
        account, created = _upsert_account(session, case_id, record, source.id)
        if created:
            summary.accounts_created += 1
        else:
            summary.accounts_updated += 1
        _upsert_posts(session, account, posts, summary)
        summary.account_ids.append(account.id)
        refs[key] = account.entity_id
    for alias, key in aliases.items():
        refs[alias] = refs[key]

    for rec in result.entities:
        label = rec.label.strip()
        if rec.type not in ENTITY_TYPES or not label:
            summary.warnings.append(f"Entidad inválida ({rec.type!r}, {rec.label!r}); se omitió.")
            continue
        label = label[:500]
        entity = session.execute(
            select(Entity).where(
                Entity.case_id == case_id, Entity.type == rec.type, func.lower(Entity.label) == label.lower()
            ).order_by(Entity.id)
        ).scalars().first()
        if entity is None:
            entity = Entity(
                case_id=case_id, type=rec.type, label=label, props=dict(rec.props),
                confidence=clamp01(rec.confidence), status=entity_status, source_id=source.id,
            )
            session.add(entity)
            session.flush()
            summary.entities_created += 1
        else:
            summary.entities_existing += 1
        if rec.ref:
            refs[rec.ref] = entity.id
        refs.setdefault(f"{rec.type}:{label}", entity.id)

    for rec in result.relations:
        rel_type = rec.type.strip()[:64]
        src_id, dst_id = refs.get(rec.src_ref), refs.get(rec.dst_ref)
        if not rel_type or src_id is None or dst_id is None or src_id == dst_id:
            summary.warnings.append(
                f"Relación {rec.src_ref!r} -[{rec.type}]-> {rec.dst_ref!r} con referencias inválidas; se omitió."
            )
            continue
        relation = session.execute(
            select(Relation).where(
                Relation.case_id == case_id, Relation.src_id == src_id,
                Relation.dst_id == dst_id, Relation.type == rel_type,
            )
        ).scalars().first()
        if relation is None:
            session.add(Relation(
                case_id=case_id, src_id=src_id, dst_id=dst_id, type=rel_type, props=dict(rec.props),
                confidence=clamp01(rec.confidence), status=entity_status, source_id=source.id,
            ))
            session.flush()
            summary.relations_created += 1
        else:
            summary.relations_existing += 1

    session.flush()
    auditlog.record(
        session, "collection.ingest", user_id=user_id, case_id=case_id, target=f"source:{source.id}",
        detail={
            "connector": result.connector, "reference": result.reference, "sha256": sha,
            **summary.model_dump(include={
                "source_created", "accounts_created", "accounts_updated", "posts_created", "posts_updated",
                "entities_created", "relations_created",
            }),
        },
    )
    return summary
