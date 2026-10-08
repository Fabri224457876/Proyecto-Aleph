"""Contraseñas (argon2id) y tokens de acceso (JWT HS256)."""

import logging
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

log = logging.getLogger("aleph.api.security")

ALGORITHM = "HS256"
ISSUER = "aleph"
DEV_SECRET = "dev-only-change-me"
MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHasher()
_dummy_hash: str | None = None
_warned_dev_secret = False


class TokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (Argon2Error, InvalidHashError):
        return False


def burn_verification(password: str) -> None:
    """Gasta el mismo tiempo que una verificación real, para no revelar si el usuario existe."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash(secrets.token_urlsafe(24))
    verify_password(_dummy_hash, password)


def create_token(user_id: int, role: str, settings) -> tuple[str, int]:
    """Devuelve (token, segundos de validez)."""
    global _warned_dev_secret
    if settings.secret_key == DEV_SECRET and not _warned_dev_secret:
        log.warning("ALEPH_SECRET_KEY tiene el valor de desarrollo: definí una clave propia antes de desplegar.")
        _warned_dev_secret = True
    now = datetime.now(UTC)
    expires_in = int(settings.token_minutes) * 60
    payload = {
        "sub": str(user_id), "role": role, "iss": ISSUER,
        "iat": now, "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM), expires_in


def decode_token(token: str, settings) -> dict:
    try:
        return jwt.decode(
            token, settings.secret_key, algorithms=[ALGORITHM], issuer=ISSUER,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
