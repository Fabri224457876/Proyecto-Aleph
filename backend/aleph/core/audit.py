"""Auditoría inmutable: cada evento incluye el hash del anterior.

Alterar o borrar un evento rompe la cadena y `verify_chain` lo detecta.
"""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import AuditEvent

GENESIS = "0" * 64
_MAX_RETRIES = 20


def _digest(prev_hash: str, ts: str, user_id, case_id, action: str, target: str, detail: dict) -> str:
    payload = json.dumps(
        [prev_hash, ts, user_id, case_id, action, target, detail],
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record(
    session: Session,
    action: str,
    *,
    user_id: int | None = None,
    case_id: int | None = None,
    target: str = "",
    detail: dict | None = None,
) -> AuditEvent:
    """Agrega un evento a la cadena. El llamador hace commit."""
    detail = detail or {}
    # prev_hash es UNIQUE: si otro proceso agregó un evento entre la lectura y el insert,
    # el insert falla y se reintenta sobre el nuevo último evento.
    for _ in range(_MAX_RETRIES):
        last = session.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)).scalar_one_or_none()
        prev_hash = last.hash if last else GENESIS
        ts = datetime.now(UTC).isoformat()
        event = AuditEvent(
            ts=ts, user_id=user_id, case_id=case_id, action=action, target=target, detail=detail,
            prev_hash=prev_hash, hash=_digest(prev_hash, ts, user_id, case_id, action, target, detail),
        )
        try:
            with session.begin_nested():
                session.add(event)
            return event
        except IntegrityError:
            continue
    raise RuntimeError("No se pudo agregar el evento de auditoría: demasiada contención")


def verify_chain(session: Session) -> tuple[bool, int | None]:
    """Devuelve (True, None) si la cadena está íntegra, o (False, id del primer evento roto)."""
    prev_hash = GENESIS
    for ev in session.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars():
        expected = _digest(prev_hash, ev.ts, ev.user_id, ev.case_id, ev.action, ev.target, ev.detail)
        if ev.prev_hash != prev_hash or ev.hash != expected:
            return False, ev.id
        prev_hash = ev.hash
    return True, None
