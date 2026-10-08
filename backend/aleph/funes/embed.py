"""Embeddings por lotes y búsqueda por similitud coseno en memoria (NumPy).

Pensado para búsqueda semántica dentro de un caso (cientos o miles de fragmentos). Para volumen
mayor está Qdrant, que es de otro paquete.
"""

from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from aleph.funes.client import FunesClient, LLMError


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """Normaliza cada fila a norma 1. Las filas nulas quedan en cero."""
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix[np.newaxis, :]
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)


async def embed_texts(client: FunesClient, texts: list[str], *, batch_size: int = 32,
                      max_chars: int = 8000) -> np.ndarray:
    """Matriz (n, d) de embeddings normalizados, una fila por texto y en el mismo orden.

    Los textos se recortan a `max_chars`. Los vacíos no se mandan al servidor: quedan como
    vector nulo (similitud 0 con todo).
    """
    if batch_size <= 0:
        raise ValueError("batch_size debe ser positivo")
    prepared = [(i, " ".join(t.split())[:max_chars]) for i, t in enumerate(texts)]
    pending = [(i, t) for i, t in prepared if t]
    vectors: dict[int, list[float]] = {}
    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        for (index, _), vector in zip(batch, await client.embed([t for _, t in batch])):
            vectors[index] = vector
    if not vectors:
        return np.zeros((len(texts), 0), dtype=np.float32)
    dims = {len(v) for v in vectors.values()}
    if len(dims) != 1:
        raise LLMError(f"El endpoint devolvió embeddings de dimensiones distintas: {sorted(dims)}")
    matrix = np.zeros((len(texts), dims.pop()), dtype=np.float32)
    for index, vector in vectors.items():
        matrix[index] = vector
    return normalize_rows(matrix)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Matriz de similitud coseno entre las filas de `a` y las de `b`."""
    return normalize_rows(a) @ normalize_rows(b).T


class SearchHit(BaseModel):
    id: str
    score: float
    payload: dict[str, Any] = Field(default_factory=dict)


class SemanticIndex:
    """Índice vectorial en memoria. No persiste nada: se arma por caso y se descarta."""

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.payloads: list[dict[str, Any]] = []
        self._matrix: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self.ids)

    def add(self, ids: list[str], vectors: np.ndarray,
            payloads: list[dict[str, Any]] | None = None) -> None:
        vectors = normalize_rows(vectors)
        if len(ids) != vectors.shape[0]:
            raise ValueError("ids y vectors tienen que tener el mismo largo")
        if payloads is not None and len(payloads) != len(ids):
            raise ValueError("payloads e ids tienen que tener el mismo largo")
        if not ids:
            return
        if self._matrix is None:
            self._matrix = vectors
        elif self._matrix.shape[1] != vectors.shape[1]:
            raise ValueError("dimensión de embedding distinta a la del índice")
        else:
            self._matrix = np.vstack([self._matrix, vectors])
        self.ids += ids
        self.payloads += payloads if payloads is not None else [{} for _ in ids]

    async def add_texts(self, client: FunesClient, ids: list[str], texts: list[str],
                        payloads: list[dict[str, Any]] | None = None, *,
                        batch_size: int = 32) -> None:
        if len(ids) != len(texts):
            raise ValueError("ids y texts tienen que tener el mismo largo")
        vectors = await embed_texts(client, texts, batch_size=batch_size)
        if vectors.shape[1]:
            self.add(ids, vectors, payloads)

    def search_vector(self, vector: np.ndarray, k: int = 5,
                      min_score: float | None = None) -> list[SearchHit]:
        if self._matrix is None or k <= 0:
            return []
        query = normalize_rows(vector)[0]
        if query.shape[0] != self._matrix.shape[1]:
            raise ValueError("dimensión de la consulta distinta a la del índice")
        scores = self._matrix @ query
        k = min(k, len(self.ids))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top], kind="stable")]
        return [SearchHit(id=self.ids[i], score=float(scores[i]), payload=self.payloads[i])
                for i in top if min_score is None or scores[i] >= min_score]

    async def search(self, client: FunesClient, query: str, k: int = 5,
                     min_score: float | None = None) -> list[SearchHit]:
        vectors = await embed_texts(client, [query])
        if not vectors.shape[1]:
            return []
        return self.search_vector(vectors[0], k, min_score)
