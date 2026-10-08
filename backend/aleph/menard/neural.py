"""Punto de extensión para la señal neuronal (familia "neural").

MENARD no implementa embeddings: otro paquete (p. ej. FUNES con GPU) registra acá un callable que
recibe las cuentas elegibles y devuelve una matriz N×N de similitudes en 0..1. Ejemplo:

    from aleph.menard import NeuralScores, register_neural_signal

    def style_embeddings(profiles):
        emb = my_model.encode(profiles)            # N×D, normalizado
        return NeuralScores(score=(emb @ emb.T + 1) / 2)

    register_neural_signal(style_embeddings, name="style_embeddings")

La señal entra a la fusión como cualquier otra. Mientras no se reentrenen los pesos con ella
(`python -m aleph.menard.eval --train`), usa el peso de reserva de la familia "neural".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence, runtime_checkable

import numpy as np

from aleph.core.schemas import AccountProfile, Evidence


@dataclass
class NeuralScores:
    score: np.ndarray  # N×N, simétrica, 0..1, en el mismo orden que los perfiles recibidos
    available: np.ndarray | None = None  # N×N bool; None = disponible para todos los pares
    explanation: str = "Similitud de embeddings de estilo."
    # Opcional: evidencia legible para el par (i, j)
    evidence: Callable[[int, int], list[Evidence]] | None = None


@runtime_checkable
class NeuralSignal(Protocol):
    def __call__(self, profiles: Sequence[AccountProfile]) -> NeuralScores: ...


_REGISTRY: dict[str, NeuralSignal] = {}


def register_neural_signal(fn: NeuralSignal, name: str | None = None) -> str:
    """Registra una señal neuronal. Devuelve el nombre completo ("neural.<name>")."""
    full = "neural." + (name or getattr(fn, "__name__", "custom"))
    _REGISTRY[full] = fn
    return full


def unregister_neural_signal(name: str) -> None:
    _REGISTRY.pop(name if name.startswith("neural.") else "neural." + name, None)


def registered_neural_signals() -> dict[str, NeuralSignal]:
    return dict(_REGISTRY)
