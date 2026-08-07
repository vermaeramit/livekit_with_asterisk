"""Encryption for provider API keys stored in Postgres.

This module lives in agent/ but is used from BOTH sides: the agent decrypts a
campaign's keys when a call starts, and the admin API encrypts them when someone
saves one. admin/docker-compose.yml already mounts ../agent at /app/kblib for
exactly this reason (see admin/backend/app/kblib.py), so there is one
implementation rather than two that drift.

Fernet, not something hand-rolled: it is authenticated (a tampered ciphertext
raises rather than decrypting to garbage) and it carries its own version byte, so
a future key rotation scheme has somewhere to hang.

SECRETS_KEY is read lazily rather than at import. The agent imports this module
in every job process, and a worker that dies at import time because an unrelated
env var is missing is far harder to diagnose than one that raises at the point
of use.
"""
from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

_fernet: Optional[Fernet] = None


class SecretsKeyMissing(RuntimeError):
    """SECRETS_KEY is absent or malformed.

    Deliberately distinct from a decryption failure. A missing key is a
    deployment fault affecting every client at once; a bad token is one row.
    """


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        raw = os.getenv("SECRETS_KEY", "").strip()
        if not raw:
            raise SecretsKeyMissing(
                "SECRETS_KEY is not set. Generate one with:\n"
                "  python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"\n"
                "and add it to /opt/aivoice/.env. Losing it makes every stored "
                "provider key unrecoverable - they must then be re-entered."
            )
        try:
            _fernet = Fernet(raw.encode())
        except (ValueError, TypeError) as e:
            raise SecretsKeyMissing(
                f"SECRETS_KEY is not a valid Fernet key: {e}"
            ) from None
    return _fernet


def encrypt(plaintext: str) -> str:
    """-> a Fernet token, safe to store."""
    if not plaintext:
        raise ValueError("refusing to encrypt an empty secret")
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """-> the plaintext key.

    Raises InvalidToken if SECRETS_KEY has changed since the value was written,
    which is the one failure mode worth telling apart from "no key configured":
    the row exists, so the console shows the client as configured, but nothing
    can read it.
    """
    return _cipher().decrypt(token.encode()).decode()


def hint(plaintext: str) -> str:
    """The only part of a key that is ever displayed or logged.

    Four characters is enough to answer "is this the key I just pasted?" and
    useless on its own. Short keys are not padded - showing 4 of a 6-character
    string would give most of it away.
    """
    return plaintext[-4:] if len(plaintext) >= 12 else "****"


def masked(plaintext: str) -> str:
    """For humans: sk-...4f2a. Never returned by the API - use hint()."""
    return f"...{hint(plaintext)}"


__all__ = ["encrypt", "decrypt", "hint", "masked",
           "SecretsKeyMissing", "InvalidToken"]
