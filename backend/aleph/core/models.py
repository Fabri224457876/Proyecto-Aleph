"""Modelo de datos persistente. Contrato compartido: lo edita solo el orquestador."""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="analyst")  # admin | analyst | auditor
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Propósito y base legal: obligatorio para abrir un caso (Ley 25.326)
    legal_basis: Mapped[str] = mapped_column(Text)
    tlp: Mapped[str] = mapped_column(String(16), default="amber")  # clear | green | amber | amber+strict | red
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed | archived
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Source(Base):
    """Procedencia de un dato: de dónde salió, cuándo, y hash del crudo."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # connector | upload | manual | funes | menard | enrichment
    connector: Mapped[str] = mapped_column(String(64), default="")
    reference: Mapped[str] = mapped_column(Text, default="")  # URL, nombre de archivo, consulta
    # Admiralty Code: fiabilidad de la fuente (A-F) y credibilidad del dato (1-6)
    reliability: Mapped[str] = mapped_column(String(1), default="F")
    credibility: Mapped[str] = mapped_column(String(1), default="6")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    raw_path: Mapped[str] = mapped_column(Text, default="")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (Index("ix_entities_case_type", "case_id", "type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))  # ver schemas.ENTITY_TYPES
    label: Mapped[str] = mapped_column(String(500))
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")  # proposed | confirmed | rejected
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Relation(Base):
    __tablename__ = "relations"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    src_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    dst_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Account(Base):
    """Cuenta en una plataforma. Siempre tiene una Entity de tipo 'account' asociada."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("case_id", "platform", "handle"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), nullable=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    platform: Mapped[str] = mapped_column(String(32))
    handle: Mapped[str] = mapped_column(String(200))
    platform_uid: Mapped[str] = mapped_column(String(200), default="")
    display_name: Mapped[str] = mapped_column(String(500), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    created_at_platform: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    following: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avatar_phash: Mapped[str] = mapped_column(String(64), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    posts: Mapped[list["Post"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("account_id", "platform_post_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    platform_post_id: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lang: Mapped[str] = mapped_column(String(8), default="")
    kind: Mapped[str] = mapped_column(String(16), default="original")  # original | reply | repost | quote
    reply_to: Mapped[str] = mapped_column(String(200), default="")  # handle al que responde
    mentions: Mapped[list] = mapped_column(JSON, default=list)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    urls: Mapped[list] = mapped_column(JSON, default=list)
    client: Mapped[str] = mapped_column(String(100), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    account: Mapped[Account] = relationship(back_populates="posts")


class AccountLink(Base):
    """Hipótesis de MENARD: dos cuentas con posible mismo operador. Requiere revisión humana."""

    __tablename__ = "account_links"
    __table_args__ = (UniqueConstraint("account_a_id", "account_b_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    account_a_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    account_b_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    score: Mapped[float] = mapped_column(Float)
    signals: Mapped[dict] = mapped_column(JSON, default=dict)  # schemas.PairResult serializado
    review_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | confirmed | rejected
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(64))  # collect | menard | funes_ner | enrich | ...
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Registro de auditoría encadenado por hash. Solo se agrega; ver core/audit.py."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[str] = mapped_column(String(40))  # ISO 8601 UTC, texto para que el hash sea estable
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    # unique: dos escritores concurrentes no pueden colgar del mismo evento (la cadena no se bifurca)
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class CaseSection(Base):
    """Inciso/carpeta del expediente donde el analista ordena los hallazgos."""

    __tablename__ = "case_sections"
    __table_args__ = (UniqueConstraint("case_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)


class Finding(Base):
    """Datachunk que el analista decidió incorporar al expediente. Siempre con procedencia."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    section_id: Mapped[int | None] = mapped_column(ForeignKey("case_sections.id"), nullable=True, index=True)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), nullable=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(Text)
    chunk: Mapped[dict] = mapped_column(JSON, default=dict)  # schemas.Datachunk serializado
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
