"""Envoltorio de `core.audit.record` para la API y los workers.

Agrega dos cosas sobre el núcleo:

1. Normaliza `detail` a JSON puro, para que el hash calculado al escribir coincida con el
   que se recalcula al leer de la base (claves de texto, sin fechas ni tuplas).
2. Serializa las escrituras de la cadena dentro del proceso. `core.audit.record` lee el
   último evento y después inserta: dos pedidos simultáneos leerían el mismo `prev_hash`
   y la cadena quedaría bifurcada. Una sesión "custodiada" toma un candado global en su
   primer flush (o en su primer evento de auditoría) y lo suelta al terminar la transacción.

El candado es por proceso: con varios procesos (API + worker, o varios workers de uvicorn)
hace falta además una garantía a nivel de base de datos en `core.audit` (ver informe WP2).
"""

import json
import threading

from sqlalchemy import event
from sqlalchemy.orm import Session

from aleph.core import audit as core_audit
from aleph.core.models import AuditEvent

from .errors import Busy

LOCK_TIMEOUT_SECONDS = 30.0

_chain_lock = threading.Lock()
_GUARDED = "aleph_chain_guard"
_HELD = "aleph_chain_held"


def guard(session: Session) -> Session:
    """Marca la sesión para que sus escrituras se serialicen con la cadena de auditoría."""
    session.info[_GUARDED] = True
    return session


def _acquire(session: Session) -> None:
    if session.info.get(_HELD):
        return
    if not _chain_lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
        raise Busy("El sistema está ocupado registrando otra operación. Probá de nuevo en unos segundos.")
    session.info[_HELD] = True


@event.listens_for(Session, "before_flush")
def _lock_before_flush(session: Session, flush_context, instances) -> None:
    if session.info.get(_GUARDED):
        _acquire(session)


@event.listens_for(Session, "after_transaction_end")
def _release_after_transaction(session: Session, transaction) -> None:
    if transaction.parent is None and session.info.pop(_HELD, False):
        _chain_lock.release()


def record(
    session: Session,
    action: str,
    *,
    user_id: int | None = None,
    case_id: int | None = None,
    target: str = "",
    detail: dict | None = None,
) -> AuditEvent:
    """Agrega un evento a la cadena en la transacción en curso. El llamador hace commit."""
    guard(session)
    _acquire(session)
    clean = json.loads(json.dumps(detail or {}, default=str, ensure_ascii=False))
    return core_audit.record(
        session, action[:64], user_id=user_id, case_id=case_id, target=str(target)[:200], detail=clean
    )
