"""Vector inicial de cada entidad: texto, tipo y grado."""

import math
import re
import unicodedata
import zlib

import numpy as np

from .schemas import TlonEdge, TlonNode

NGRAM_DIM = 512


def fold(text: str) -> str:
    """Minúsculas y sin tildes: 'Auditoría' y 'auditoria' quedan iguales."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def node_text(node: TlonNode) -> str:
    values = [str(v) for v in node.props.values()
              if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip()]
    return " ".join([node.type, node.label, *values[:12], node.text[:2000]]).strip()


def ngram_vector(text: str, dim: int = NGRAM_DIM) -> np.ndarray:
    """N-gramas de caracteres (3 y 4) con hashing firmado. Determinístico y sin dependencias."""
    vec = np.zeros(dim)
    clean = re.sub(r"\s+", " ", fold(text)).strip()
    padded = f" {clean} "
    for n in (3, 4):
        for i in range(len(padded) - n + 1):
            h = zlib.crc32(padded[i:i + n].encode("utf-8"))
            vec[h % dim] += 1.0 if (h >> 16) & 1 else -1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def _l2(rows: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return rows / norms


def build_features(nodes: list[TlonNode], edges: list[TlonEdge],
                   vectors: dict[int, list[float]]) -> tuple[np.ndarray, str]:
    """Matriz (n_nodos × d) y de dónde salió el texto: embeddings, ngramas o mixto."""
    # Los embeddings aportan significado; los n-gramas, identidad (nombres, dominios, usuarios).
    # Van siempre juntos: con embeddings solos, entidades distintas de un mismo tema se confunden.
    have = [n.id in vectors for n in nodes]
    texts = np.vstack([ngram_vector(node_text(n)) for n in nodes])
    if any(have):
        source = "embeddings" if all(have) else "mixto"
        width = len(next(vectors[n.id] for n, h in zip(nodes, have) if h))
        emb = np.vstack([np.asarray(vectors[n.id], dtype=float) if h else np.zeros(width)
                         for n, h in zip(nodes, have)])
        text_block = np.hstack([_l2(emb), texts]) / math.sqrt(2)
    else:
        source = "ngramas"
        text_block = texts

    types = sorted({n.type for n in nodes})
    type_block = np.zeros((len(nodes), len(types)))
    for i, n in enumerate(nodes):
        type_block[i, types.index(n.type)] = 1.0

    index = {n.id: i for i, n in enumerate(nodes)}
    degree = np.zeros(len(nodes))
    for e in edges:
        if e.src in index and e.dst in index:
            degree[index[e.src]] += 1
            degree[index[e.dst]] += 1
    degree_block = (np.log1p(degree) / max(1.0, math.log1p(degree.max() if len(degree) else 1)))[:, None]

    features = np.hstack([text_block, 0.5 * type_block, 0.25 * degree_block])
    return features, source
