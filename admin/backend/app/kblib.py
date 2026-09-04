"""Bridge to the agent's knowledge-base module.

agent/kb.py is mounted read-only into this container rather than copied. There
is exactly one implementation of extraction, chunking and embedding: if the panel
chunked differently from the CLI, retrieval quality would drift between documents
ingested by different routes and nothing would ever say so.

kb.py imports the agent's store module for its pool, which reads DATABASE_URL -
the same value this service already has.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("admin-api")

AGENT_LIB = Path("/app/kblib")

_kb = None
_import_error: str | None = None


def available() -> bool:
    return _kb is not None


def why_unavailable() -> str:
    return _import_error or "unknown"


def kb():
    if _kb is None:
        raise RuntimeError(f"knowledge base support unavailable: {_import_error}")
    return _kb


def agent_module(name: str):
    """Any module out of the mounted agent directory, by name.

    The path is already on sys.path once kb has loaded, so this is an import
    and not a second mounting mechanism. Used by the chat tester, which needs
    `store` and `chat` for the same reason it needs `kb`: running the agent's
    OWN code is the only thing that makes it a test rather than a lookalike.
    """
    if _kb is None:
        raise RuntimeError(f"agent library unavailable: {_import_error}")
    import importlib

    return importlib.import_module(name)


def _load():
    global _kb, _import_error
    if not AGENT_LIB.is_dir():
        _import_error = (
            f"{AGENT_LIB} is not mounted - add "
            "'../agent:/app/kblib:ro' to the admin-api volumes"
        )
        return
    # kb.py does `import store`, so its directory has to be importable as a
    # top-level path, not just as a package.
    if str(AGENT_LIB) not in sys.path:
        sys.path.insert(0, str(AGENT_LIB))
    try:
        import kb as _mod  # type: ignore[import-not-found]

        _kb = _mod
    except Exception as e:  # missing OPENAI_API_KEY surfaces later, not here
        _import_error = f"{type(e).__name__}: {e}"
        log.warning("knowledge base module not loaded: %s", _import_error)


_load()
