"""Bridge to the agent's crypto module.

Same arrangement as kblib.py: agent/ is mounted read-only at /app/kblib, and
agent/crypto.py is imported from there rather than copied. Two copies of an
encryption helper is how you end up with a panel that encrypts one way and an
agent that cannot read it back - and that failure is invisible until a call
drops, because the console shows the key as configured either way.

Unlike kblib, a failure here is fatal to the feature rather than degraded: if
this cannot load, provider keys cannot be written OR read, and every campaign
resolves to "no key". So it raises rather than returning a null object.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("admin-api")

AGENT_LIB = Path("/app/kblib")

_crypto = None
_import_error: str | None = None


def available() -> bool:
    return _crypto is not None


def why_unavailable() -> str:
    return _import_error or "unknown"


def crypto():
    if _crypto is None:
        raise RuntimeError(f"provider key encryption unavailable: {_import_error}")
    return _crypto


def toolfmt():
    """agent/toolfmt.py, from the same mount.

    Placeholder substitution and response extraction, shared with the agent so
    the console's "this is what the model would receive" cannot drift from what
    the model actually receives.
    """
    _load()  # no-op if already loaded; ensures AGENT_LIB is on sys.path
    import toolfmt as _mod  # type: ignore[import-not-found]

    return _mod


def _load():
    global _crypto, _import_error
    if not AGENT_LIB.is_dir():
        _import_error = (
            f"{AGENT_LIB} is not mounted - add "
            "'../agent:/app/kblib:ro' to the admin-api volumes"
        )
        log.warning("provider key encryption not loaded: %s", _import_error)
        return
    if str(AGENT_LIB) not in sys.path:
        sys.path.insert(0, str(AGENT_LIB))
    try:
        import crypto as _mod  # type: ignore[import-not-found]

        _crypto = _mod
    except Exception as e:
        # A missing SECRETS_KEY does NOT surface here - crypto.py reads it
        # lazily, so it fails on first use with a message that says what to do.
        _import_error = f"{type(e).__name__}: {e}"
        log.warning("provider key encryption not loaded: %s", _import_error)


_load()
