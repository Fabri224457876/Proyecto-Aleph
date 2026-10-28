"""TLÖN: hipótesis de vínculos con un modelo predictivo de representaciones (estilo JEPA).

*Tlön, Uqbar, Orbis Tertius*: un mundo que se infiere a partir de fragmentos. TLÖN aprende un
modelo del mundo de cada caso y propone los vínculos que ese mundo sugiere y todavía nadie
registró. Como MENARD y FUNES, recibe y devuelve esquemas: no toca la base ni la red.

Cómo funciona (ver `model.py`):
- Cada entidad se describe con un vector: su texto (embeddings locales si están, o n-gramas de
  caracteres si no), su tipo y su grado en el grafo.
- Un codificador de contexto resume a una entidad junto con sus vecinos. Un predictor, a partir
  de ese contexto y del tipo de relación, predice la *representación* de la entidad del otro
  extremo; no reconstruye su texto. El objetivo se calcula con un codificador objetivo que es un
  promedio móvil (EMA) del de contexto, como en I-JEPA, y un término de varianza evita el colapso.
- Lo que el modelo predice con fuerza y no existe en el grafo es una hipótesis. Cada una trae su
  evidencia (vecinos parecidos, vecinos en común, atributos compartidos) y la calidad que el
  modelo mostró sobre vínculos reales que se le ocultaron al entrenar.

Las salidas son hipótesis para revisión humana, nunca veredictos.
"""

from .engine import propose
from .schemas import TlonEdge, TlonInput, TlonNode, TlonReport

__all__ = ["TlonEdge", "TlonInput", "TlonNode", "TlonReport", "propose"]
