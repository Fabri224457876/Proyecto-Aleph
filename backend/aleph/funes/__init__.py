"""Motor FUNES: IA 100% local de Aleph.

Todo lo que devuelve FUNES es una propuesta para revisión humana. El LLM propone; la validación
determinística (citas contra el texto fuente, ids contra los datos de entrada, listas cerradas)
decide qué pasa.

    client.py          cliente de chat/embeddings para endpoints del cluster
    ner.py             entidades y relaciones (reglas + LLM)
    contradictions.py  contradicciones entre fuentes (detección determinística)
    report.py          resumen de caso y borrador de informe
    attack_map.py      técnicas ATT&CK candidatas
    embed.py           búsqueda semántica en memoria
    smoke.py           prueba de punta a punta: python -m aleph.funes.smoke
"""

from aleph.funes.attack_map import AttackMapping, Technique, TechniqueCandidate, map_techniques
from aleph.funes.client import FunesClient, LLMError, NonLocalEndpointError, split_text
from aleph.funes.contradictions import (
    Claim,
    Contradiction,
    ContradictionConfig,
    Place,
    detect_contradictions,
    explain_contradictions,
    extract_claims,
)
from aleph.funes.embed import SemanticIndex, embed_texts
from aleph.funes.ner import NerResult, extract_entities, extract_rule_entities
from aleph.funes.report import CaseData, ReportDraft, SourceRef, draft_report, summarize_case
from aleph.funes.structured import StructuredOutputError

__all__ = [
    "AttackMapping", "CaseData", "Claim", "Contradiction", "ContradictionConfig", "FunesClient",
    "LLMError", "NerResult", "NonLocalEndpointError", "Place", "ReportDraft", "SemanticIndex",
    "SourceRef", "StructuredOutputError", "Technique", "TechniqueCandidate",
    "detect_contradictions", "draft_report", "embed_texts", "explain_contradictions",
    "extract_claims", "extract_entities", "extract_rule_entities", "map_techniques",
    "split_text", "summarize_case",
]
