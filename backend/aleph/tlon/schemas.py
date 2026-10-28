"""Contrato de TLÖN: el grafo de un caso entra, las hipótesis de vínculos salen."""

from typing import Any

from pydantic import BaseModel, Field


class TlonNode(BaseModel):
    id: int
    type: str
    label: str
    props: dict[str, Any] = Field(default_factory=dict)
    text: str = ""  # texto extra para describirla (bio, publicaciones), opcional
    confirmed: bool = True


class TlonEdge(BaseModel):
    src: int
    dst: int
    type: str
    confirmed: bool = True


class TlonInput(BaseModel):
    nodes: list[TlonNode]
    edges: list[TlonEdge]
    # Vectores precomputados por id de nodo (embeddings locales). Si falta alguno se usan n-gramas.
    vectors: dict[int, list[float]] = Field(default_factory=dict)
    # Pares que un analista ya rechazó: no se vuelven a proponer.
    blocked: list[tuple[int, int]] = Field(default_factory=list)


class TlonParams(BaseModel):
    dim: int = Field(default=32, ge=4, le=256, description="Dimensión del espacio latente")
    hidden: int = Field(default=64, ge=8, le=512)
    epochs: int = Field(default=300, ge=10, le=5000)
    lr: float = Field(default=0.01, gt=0, le=1)
    ema: float = Field(default=0.98, ge=0, lt=1, description="Momento del codificador objetivo")
    holdout: float = Field(default=0.15, ge=0, le=0.5, description="Vínculos ocultos para medir calidad")
    max_hypotheses: int = Field(default=15, ge=1, le=200)
    min_percentile: float = Field(default=0.5, ge=0, le=1,
                                  description="Solo hipótesis más fuertes que este percentil de los vínculos reales")
    seed: int = 7


class Quality(BaseModel):
    """Qué tan bien predijo el modelo vínculos reales que se le ocultaron al entrenar."""

    holdout_edges: int
    mrr: float | None = Field(description="Rango recíproco medio del extremo verdadero (1 = siempre primero)")
    hits_at_5: float | None = Field(description="Proporción de vínculos ocultos con el extremo real entre los 5 primeros")
    baseline_hits_at_5: float | None = Field(description="Lo mismo eligiendo al azar, para comparar")
    final_loss: float
    feature_source: str = Field(description="embeddings | ngramas | mixto")


class HypothesisEvidence(BaseModel):
    similar_neighbors: list[str] = Field(
        default_factory=list, description="Vecinos del origen que el modelo ve parecidos al destino")
    common_neighbors: list[str] = Field(default_factory=list)
    shared_attributes: list[str] = Field(default_factory=list)
    explanation: str = ""


class Hypothesis(BaseModel):
    src: int
    dst: int
    src_label: str
    dst_label: str
    type: str = Field(description="Tipo de relación que el modelo predice")
    score: float = Field(description="Similitud coseno entre la predicción y el destino (−1 a 1)")
    percentile: float = Field(description="Qué proporción de los vínculos reales del mismo tipo puntúa por debajo (0 a 1)")
    evidence: HypothesisEvidence


class TlonReport(BaseModel):
    status: str = Field(description="ok | datos_insuficientes")
    message: str = ""
    nodes: int
    edges: int
    quality: Quality | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
