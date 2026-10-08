"""Modelos de entrada y salida de la API."""

import re
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from aleph.core.schemas import ENTITY_TYPES, TLP_LEVELS

from .security import MIN_PASSWORD_LENGTH
from .services.util import as_utc

T = TypeVar("T")

ROLES = ("admin", "analyst", "auditor")
CASE_STATUSES = ("open", "closed", "archived")
ITEM_STATUSES = ("proposed", "confirmed", "rejected")
SOURCE_KINDS = ("connector", "upload", "manual", "funes", "menard", "enrichment")
# Admiralty Code (STANAG 2511): fiabilidad de la fuente A-F, credibilidad del dato 1-6
RELIABILITY = {
    "A": "Completamente fiable", "B": "Normalmente fiable", "C": "Bastante fiable",
    "D": "Normalmente no fiable", "E": "No fiable", "F": "No se puede juzgar",
}
CREDIBILITY = {
    "1": "Confirmado por otras fuentes", "2": "Probablemente cierto", "3": "Posiblemente cierto",
    "4": "Dudoso", "5": "Improbable", "6": "No se puede juzgar",
}

Role = Literal["admin", "analyst", "auditor"]
UTCDatetime = Annotated[datetime, AfterValidator(as_utc)]
OptUTCDatetime = Annotated[datetime | None, AfterValidator(as_utc)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """Sobre de paginación común a todos los listados."""

    items: list[T]
    total: int
    limit: int
    offset: int


class Message(BaseModel):
    detail: str


class FieldError(BaseModel):
    field: str
    message: str


class ValidationErrorOut(BaseModel):
    detail: str
    errors: list[FieldError] = Field(default_factory=list)


def _not_blank(value: str, what: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{what} no puede estar vacío.")
    return value


def _tlp(value: str) -> str:
    norm = value.strip().lower().removeprefix("tlp:")
    if norm not in TLP_LEVELS:
        raise ValueError(f"TLP inválido. Valores permitidos: {', '.join(TLP_LEVELS)}.")
    return norm


def _entity_type(value: str) -> str:
    norm = value.strip().lower()
    if norm not in ENTITY_TYPES:
        raise ValueError(f"Tipo de entidad inválido. Valores permitidos: {', '.join(ENTITY_TYPES)}.")
    return norm


def _reliability(value: str) -> str:
    norm = value.strip().upper()
    if norm not in RELIABILITY:
        raise ValueError("Fiabilidad inválida: el Admiralty Code admite de A a F.")
    return norm


def _credibility(value: str | int) -> str:
    norm = str(value).strip()
    if norm not in CREDIBILITY:
        raise ValueError("Credibilidad inválida: el Admiralty Code admite de 1 a 6.")
    return norm


def _password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
    if len(value) > 256:
        raise ValueError("La contraseña es demasiado larga.")
    return value


def _username(value: str) -> str:
    norm = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", norm):
        raise ValueError("Usuario inválido: de 3 a 64 caracteres entre letras, números, punto, guion y guion bajo.")
    return norm


TLP = Annotated[str, AfterValidator(_tlp), Field(json_schema_extra={"enum": list(TLP_LEVELS)})]
EntityType = Annotated[str, AfterValidator(_entity_type), Field(json_schema_extra={"enum": list(ENTITY_TYPES)})]
Reliability = Annotated[str, AfterValidator(_reliability), Field(json_schema_extra={"enum": list(RELIABILITY)})]
Credibility = Annotated[
    str | int, AfterValidator(_credibility), Field(json_schema_extra={"enum": list(CREDIBILITY)})
]
Password = Annotated[str, AfterValidator(_password)]
Username = Annotated[str, AfterValidator(_username)]


# ---------------------------------------------------------------- auth y usuarios

class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(Out):
    id: int
    username: str
    role: str
    active: bool
    created_at: UTCDatetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Segundos de validez del token")
    user: UserOut


class UserCreate(BaseModel):
    username: Username
    password: Password
    role: Role = "analyst"


class UserUpdate(BaseModel):
    role: Role | None = None
    active: bool | None = None
    password: Password | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: Password


# ---------------------------------------------------------------- casos

class CaseCreate(BaseModel):
    name: str = Field(max_length=200)
    description: str = ""
    legal_basis: str = Field(description="Propósito y base legal de la investigación (obligatorio, Ley 25.326)")
    tlp: TLP = "amber"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _not_blank(v, "El nombre del caso")

    @field_validator("legal_basis")
    @classmethod
    def _legal(cls, v: str) -> str:
        return _not_blank(v, "El propósito y base legal")


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    legal_basis: str | None = None
    tlp: TLP | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return v if v is None else _not_blank(v, "El nombre del caso")

    @field_validator("legal_basis")
    @classmethod
    def _legal(cls, v: str | None) -> str | None:
        return v if v is None else _not_blank(v, "El propósito y base legal")


class CaseOut(Out):
    id: int
    name: str
    description: str
    legal_basis: str
    tlp: str
    status: str
    created_by: int
    created_at: UTCDatetime


class CaseDetail(CaseOut):
    counts: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------- fuentes

class SourceCreate(BaseModel):
    kind: Literal["connector", "upload", "manual", "funes", "menard", "enrichment"] = "manual"
    connector: str = Field(default="", max_length=64)
    reference: str = Field(default="", description="URL, nombre de archivo o consulta de origen")
    reliability: Reliability = "F"
    credibility: Credibility = "6"
    sha256: str = Field(default="", pattern=r"^([0-9a-fA-F]{64})?$")
    retrieved_at: datetime | None = None


class SourceOut(Out):
    id: int
    case_id: int
    kind: str
    connector: str
    reference: str
    reliability: str
    credibility: str
    admiralty: str = Field(default="", description="Código combinado, p. ej. B2")
    sha256: str
    has_raw: bool = False
    retrieved_at: UTCDatetime
    created_by: int | None


# ---------------------------------------------------------------- grafo

class EntityCreate(BaseModel):
    type: EntityType
    label: str = Field(max_length=500)
    props: dict[str, Any] = Field(default_factory=dict)
    confidence: Confidence = 1.0
    status: Literal["proposed", "confirmed"] = "confirmed"
    source_id: int | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        return _not_blank(v, "La etiqueta")


class EntityUpdate(BaseModel):
    type: EntityType | None = None
    label: str | None = Field(default=None, max_length=500)
    props: dict[str, Any] | None = None
    confidence: Confidence | None = None
    source_id: int | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, v: str | None) -> str | None:
        return v if v is None else _not_blank(v, "La etiqueta")


class EntityOut(Out):
    id: int
    case_id: int
    type: str
    label: str
    props: dict[str, Any]
    confidence: float
    status: str
    source_id: int | None
    created_at: UTCDatetime


class RelationCreate(BaseModel):
    src_id: int
    dst_id: int
    type: str = Field(max_length=64)
    props: dict[str, Any] = Field(default_factory=dict)
    confidence: Confidence = 1.0
    status: Literal["proposed", "confirmed"] = "confirmed"
    source_id: int | None = None

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        return _not_blank(v, "El tipo de relación")

    @model_validator(mode="after")
    def _no_loop(self):
        if self.src_id == self.dst_id:
            raise ValueError("Una relación no puede unir una entidad consigo misma.")
        return self


class RelationUpdate(BaseModel):
    type: str | None = Field(default=None, max_length=64)
    props: dict[str, Any] | None = None
    confidence: Confidence | None = None
    source_id: int | None = None

    @field_validator("type")
    @classmethod
    def _type(cls, v: str | None) -> str | None:
        return v if v is None else _not_blank(v, "El tipo de relación")


class RelationOut(Out):
    id: int
    case_id: int
    src_id: int
    dst_id: int
    type: str
    props: dict[str, Any]
    confidence: float
    status: str
    source_id: int | None
    created_at: UTCDatetime


class ReviewIn(BaseModel):
    decision: Literal["accept", "reject"]
    note: str = Field(default="", max_length=2000)


class MergeIn(BaseModel):
    keep_id: int = Field(description="Entidad que se conserva")
    duplicate_id: int = Field(description="Entidad duplicada, que se absorbe y se borra")


class MergeOut(BaseModel):
    entity: EntityOut
    relations_repointed: int
    relations_removed: int
    accounts_repointed: int


class GraphNode(Out):
    id: int
    type: str
    label: str
    status: str
    confidence: float
    props: dict[str, Any]
    source_id: int | None
    degree: int = 0


class GraphEdge(BaseModel):
    id: int
    source: int
    target: int
    type: str
    status: str
    confidence: float
    props: dict[str, Any]
    source_id: int | None = Field(description="Procedencia (id de Source), no confundir con `source`")


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    counts: dict[str, int]
    truncated: bool = False


class PathOut(BaseModel):
    found: bool
    length: int | None = Field(default=None, description="Cantidad de saltos")
    nodes: list[GraphNode] = Field(default_factory=list, description="En orden, de origen a destino")
    edges: list[GraphEdge] = Field(default_factory=list)


# ---------------------------------------------------------------- cuentas y publicaciones

class AccountOut(Out):
    id: int
    case_id: int
    entity_id: int | None
    source_id: int | None
    platform: str
    handle: str
    platform_uid: str
    display_name: str
    bio: str
    url: str
    created_at_platform: OptUTCDatetime
    followers: int | None
    following: int | None
    avatar_phash: str
    meta: dict[str, Any]
    post_count: int = 0


class AccountBrief(Out):
    id: int
    platform: str
    handle: str
    display_name: str = ""
    entity_id: int | None = None


class PostOut(Out):
    id: int
    account_id: int
    platform_post_id: str
    text: str
    created_at: OptUTCDatetime
    lang: str
    kind: str
    reply_to: str
    mentions: list[Any]
    hashtags: list[Any]
    urls: list[Any]
    client: str
    meta: dict[str, Any]


class PostHit(PostOut):
    platform: str
    handle: str


class SearchOut(BaseModel):
    query: str
    entities: list[EntityOut]
    accounts: list[AccountOut]
    posts: list[PostHit]
    totals: dict[str, int]


# ---------------------------------------------------------------- MENARD

class MenardRunIn(BaseModel):
    account_ids: list[int] | None = Field(default=None, description="Subconjunto de cuentas; por defecto todas")
    min_score: Confidence = Field(default=0.0, description="No guardar pares por debajo de este puntaje")


class LinkOut(BaseModel):
    id: int
    case_id: int
    account_a: AccountBrief
    account_b: AccountBrief
    score: float
    confidence: str = Field(default="", description="baja | media | alta, según el motor")
    summary: str = Field(default="", description="Hipótesis redactada por el motor")
    signals: dict[str, Any] = Field(description="PairResult completo: desglose por señal y evidencia")
    review_status: str
    reviewed_by: int | None
    review_note: str
    created_at: UTCDatetime


class LinkReviewIn(BaseModel):
    decision: Literal["confirm", "reject"]
    note: str = Field(default="", max_length=2000)


class LinkReviewOut(BaseModel):
    link: LinkOut
    relation: RelationOut | None = None


# ---------------------------------------------------------------- trabajos, auditoría, línea de tiempo

class JobOut(Out):
    id: int
    case_id: int | None
    kind: str
    status: str
    params: dict[str, Any]
    result: dict[str, Any]
    error: str
    created_by: int | None
    created_at: UTCDatetime
    finished_at: OptUTCDatetime


class AuditEventOut(Out):
    id: int
    ts: str
    user_id: int | None
    case_id: int | None
    action: str
    target: str
    detail: dict[str, Any]
    prev_hash: str
    hash: str


class AuditVerifyOut(BaseModel):
    ok: bool
    broken_event_id: int | None = None
    total_events: int
    detail: str


class TimelineItem(BaseModel):
    kind: Literal["post", "event"]
    at: UTCDatetime
    title: str
    text: str = ""
    post_id: int | None = None
    account_id: int | None = None
    entity_id: int | None = None
    platform: str = ""
    handle: str = ""
    post_kind: str = ""
    status: str = ""
    source_id: int | None = None


class TimelineOut(Page[TimelineItem]):
    undated_events: int = Field(default=0, description="Entidades `event` sin fecha interpretable en sus props")


class ConnectorOut(BaseModel):
    name: str
    title: str
    mode: str
    params: dict[str, str]
    requires: list[str]
    available: bool = Field(description="False si falta alguna clave de configuración que el conector necesita")


class CaseExport(BaseModel):
    exported_at: UTCDatetime
    case: CaseOut
    sources: list[SourceOut]
    entities: list[EntityOut]
    relations: list[RelationOut]
    accounts: list[AccountOut]
    account_links: list[LinkOut]
    posts: list[PostOut] = Field(default_factory=list)
