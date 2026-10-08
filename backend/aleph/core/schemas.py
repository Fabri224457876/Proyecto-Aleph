"""Esquemas de intercambio entre módulos. Contrato compartido: lo edita solo el orquestador.

Los motores (menard, funes, cti, connectors) reciben y devuelven estos tipos y no tocan la base.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ENTITY_TYPES = (
    "person", "account", "email", "phone", "domain", "ip", "url", "organization",
    "location", "event", "hash", "wallet", "document", "vehicle", "malware", "vulnerability",
    "bank_account", "alias",
)
TLP_LEVELS = ("clear", "green", "amber", "amber+strict", "red")

PostKind = Literal["original", "reply", "repost", "quote"]


class PostRecord(BaseModel):
    platform_post_id: str
    text: str = ""
    created_at: datetime | None = None  # siempre con zona horaria (UTC)
    lang: str = ""
    kind: PostKind = "original"
    reply_to: str = ""
    mentions: list[str] = Field(default_factory=list)  # handles sin @, en minúscula
    hashtags: list[str] = Field(default_factory=list)  # sin #, en minúscula
    urls: list[str] = Field(default_factory=list)
    client: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class AccountRecord(BaseModel):
    platform: str
    handle: str  # sin @, tal como lo muestra la plataforma
    platform_uid: str = ""
    display_name: str = ""
    bio: str = ""
    url: str = ""
    created_at_platform: datetime | None = None
    followers: int | None = None
    following: int | None = None
    avatar_url: str = ""
    avatar_phash: str = ""
    # Opcional: handles seguidos/seguidores si el conector los pudo obtener
    following_handles: list[str] = Field(default_factory=list)
    follower_handles: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class AccountProfile(BaseModel):
    """Una cuenta con sus publicaciones: la unidad que analiza MENARD."""

    account: AccountRecord
    posts: list[PostRecord] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.account.platform}:{self.account.handle.lower()}"


class CollectionResult(BaseModel):
    """Lo que devuelve un conector."""

    connector: str
    reference: str  # URL, consulta o archivo de origen
    retrieved_at: datetime
    profiles: list[AccountProfile] = Field(default_factory=list)
    entities: list["EntityRecord"] = Field(default_factory=list)
    relations: list["RelationRecord"] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    raw: Any = None  # respuesta cruda para guardar como evidencia


class EntityRecord(BaseModel):
    type: str
    label: str
    props: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    ref: str = ""  # id local para referenciar desde RelationRecord


class RelationRecord(BaseModel):
    src_ref: str
    dst_ref: str
    type: str
    props: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class Evidence(BaseModel):
    """Ejemplo concreto que respalda una señal, legible por un analista."""

    description: str
    a: str = ""
    b: str = ""


class SignalResult(BaseModel):
    name: str  # p. ej. "stylometry.char_ngrams"
    family: Literal["stylometry", "temporal", "behavior", "network", "profile", "neural"]
    score: float  # 0..1, similitud
    weight: float = 1.0
    available: bool = True  # False si no había datos suficientes
    explanation: str = ""  # en español
    evidence: list[Evidence] = Field(default_factory=list)


class PairResult(BaseModel):
    a: str  # AccountProfile.key
    b: str
    score: float  # 0..1 fusionado y calibrado
    confidence: Literal["baja", "media", "alta"]
    signals: list[SignalResult] = Field(default_factory=list)
    summary: str = ""  # en español, redactado como hipótesis


class ClusterResult(BaseModel):
    members: list[str]
    cohesion: float
    summary: str = ""


class MenardReport(BaseModel):
    pairs: list[PairResult] = Field(default_factory=list)
    clusters: list[ClusterResult] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict)  # key -> motivo (p. ej. pocos posts)
    params: dict[str, Any] = Field(default_factory=dict)


CollectionResult.model_rebuild()


# --- Captura desde la extensión de navegador (Aleph Lens) ---


class CaptureBatch(BaseModel):
    """Lo que la extensión envía mientras el analista navega: POST /api/cases/{id}/captures"""

    page_url: str
    page_title: str = ""
    platform: str  # "x", "instagram", "bluesky", "reddit", "generic", ...
    captured_at: datetime
    profiles: list[AccountProfile] = Field(default_factory=list)
    # Interacciones vistas en pantalla: (handle_origen, handle_destino, tipo)
    interactions: list[tuple[str, str, str]] = Field(default_factory=list)
    text_snippets: list[str] = Field(default_factory=list)  # texto libre para extraer IOCs/entidades
    extension_version: str = ""


class HandleLookupRequest(BaseModel):
    """POST /api/cases/{id}/captures/lookup: qué sabe el caso de los handles visibles."""

    platform: str
    handles: list[str]


class HandleInfo(BaseModel):
    known: bool = False
    entity_id: int | None = None
    posts_captured: int = 0
    # Hipótesis MENARD que involucran a esta cuenta
    links: list[dict[str, Any]] = Field(default_factory=list)  # {other, platform, score, review_status}
    relations: list[dict[str, Any]] = Field(default_factory=list)  # {type, label, entity_id}
    note: str = ""


class HandleLookupResponse(BaseModel):
    handles: dict[str, HandleInfo] = Field(default_factory=dict)


# --- Datachunks: fragmentos resaltados que el analista arrastra al expediente ---

DEFAULT_SECTIONS = ("Identidad", "Cuentas", "Contactos", "Ubicaciones", "Actividad", "Infraestructura", "Sin clasificar")


class Datachunk(BaseModel):
    """Fragmento detectado en pantalla por Aleph Lens y resaltado para el analista."""

    kind: str  # un valor de ENTITY_TYPES, o "text" para una cita libre seleccionada a mano
    value: str  # valor normalizado (handle, email, dominio, fecha, lugar, texto...)
    quote: str = ""  # texto exacto tal como aparecía en la página
    context: str = ""  # oración o publicación que lo rodea
    page_url: str
    page_title: str = ""
    platform: str = "generic"
    author_handle: str = ""  # cuenta que lo escribió, si se conoce
    post_id: str = ""
    detected_by: Literal["rule", "funes", "manual"] = "rule"
    captured_at: datetime


class FindingCreate(BaseModel):
    """POST /api/cases/{id}/findings: el analista soltó un datachunk en un inciso."""

    section_id: int | None = None  # None = "Sin clasificar"
    chunk: Datachunk
    attach_to_entity_id: int | None = None  # si lo soltó sobre una entidad del grafo
    note: str = ""


class SectionOut(BaseModel):
    id: int
    name: str
    position: int
    findings: int = 0
