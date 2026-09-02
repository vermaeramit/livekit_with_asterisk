from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from .config import settings

_ph = PasswordHasher()


# ───────────────────────────── passwords ─────────────────────────────

def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        _ph.verify(hashed, plain)
        return True
    except (VerifyMismatchError, VerificationError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _ph.check_needs_rehash(hashed)


# ───────────────────────────── access tokens ─────────────────────────────

def create_access_token(user_id: int, tenant_id: int | None, role: str,
                       session_id: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "tid": tenant_id,
            "role": role,
            # Which session this token belongs to. The heartbeat has to say
            # WHICH row to keep alive, and sending the refresh token every
            # minute to answer that would put the long-lived credential on the
            # wire sixty times an hour.
            "sid": session_id,
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
            "typ": "access",
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, settings.jwt_secret,
                         algorithms=[settings.jwt_algorithm])
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


# ───────────────────────────── refresh tokens ─────────────────────────────
# The raw refresh token is returned to the client once and never stored. Only its
# SHA-256 lands in user_sessions, so a database leak cannot be replayed, and a
# session can be revoked without rotating the signing key.

def new_refresh_token() -> tuple[str, str]:
    """-> (raw token for the client, hash to store)"""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
