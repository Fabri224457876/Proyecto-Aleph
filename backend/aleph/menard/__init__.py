"""Motor MENARD: detección de posibles multicuentas (hipótesis de mismo operador).

API pública:
    analyze(profiles, *, min_posts=5, threshold=0.5, ...) -> MenardReport
    compare(a, b, *, background=None) -> PairResult
    register_neural_signal(fn, name=...)   # punto de extensión para embeddings (familia "neural")

Librería pura: no usa base de datos, red ni GPU. Las salidas son hipótesis con evidencia para
revisión humana, nunca afirmaciones de identidad.
"""

from .engine import (
    DEFAULT_MIN_POSTS,
    DEFAULT_THRESHOLD,
    FAMILIES,
    Analysis,
    analyze,
    analyze_matrix,
    build_clusters,
    compare,
)
from .neural import (
    NeuralScores,
    NeuralSignal,
    register_neural_signal,
    registered_neural_signals,
    unregister_neural_signal,
)

__all__ = [
    "DEFAULT_MIN_POSTS", "DEFAULT_THRESHOLD", "FAMILIES", "Analysis", "NeuralScores",
    "NeuralSignal", "analyze", "analyze_matrix", "build_clusters", "compare",
    "register_neural_signal", "registered_neural_signals", "unregister_neural_signal",
]
