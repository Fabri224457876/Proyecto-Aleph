"""Servicios de persistencia de la API, reutilizables desde los workers. No importan FastAPI.

Convención: reciben una `Session`, registran su evento de auditoría en la misma transacción
y no hacen commit (lo hace quien llama). Los errores son subclases de `aleph.api.errors.ServiceError`.
"""

from .ingest import IngestSummary, ensure_account_entity, ingest_collection
from .menard import MenardRunSummary, load_profiles, review_link, run_menard

__all__ = [
    "IngestSummary", "MenardRunSummary", "ensure_account_entity", "ingest_collection",
    "load_profiles", "review_link", "run_menard",
]
