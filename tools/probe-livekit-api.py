"""Print what this installation of livekit-agents actually offers.

Written because guessing an API and finding out on a live call is expensive.
Everything here is read-only and prints facts; nothing is imported for its side
effects and nothing is patched.

    /opt/aivoice/agent/.venv/bin/python /srv/aivoice/tools/probe-livekit-api.py

Each section is wrapped so that one missing name cannot hide the rest.
"""
from __future__ import annotations

import inspect
import re


def head(t: str) -> None:
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


def show(label, fn):
    try:
        print(f"{label}: {fn()}")
    except Exception as e:
        print(f"{label}: !! {type(e).__name__}: {e}")


head("versions")
for mod in ("livekit.agents", "livekit.plugins.soniox", "livekit.plugins.silero"):
    try:
        m = __import__(mod, fromlist=["__version__"])
        print(f"{mod:30s} {getattr(m, '__version__', '?'):12s} {m.__file__}")
    except Exception as e:
        print(f"{mod:30s} !! {type(e).__name__}: {e}")

head("AgentSession.say()")
try:
    from livekit.agents import AgentSession
    print(inspect.signature(AgentSession.say))
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("events this session emits")
try:
    from livekit.agents.voice import events as ev

    et = getattr(ev, "EventTypes", None)
    args = getattr(et, "__args__", None)
    print(sorted(args) if args else f"EventTypes = {et!r}")

    for name in sorted(n for n in dir(ev) if n.endswith("Event")):
        cls = getattr(ev, name)
        fields = list(getattr(cls, "model_fields", None)
                      or getattr(cls, "__annotations__", {}) or {})
        print(f"  {name:34s} {fields}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("what state values exist")
try:
    from livekit.agents.voice import agent_session as _as
    for n in ("UserState", "AgentState"):
        v = getattr(_as, n, None)
        print(f"{n} = {getattr(v, '__args__', v)!r}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("soniox: what we are allowed to send")
try:
    import dataclasses

    from livekit.plugins import soniox as sx
    opts = getattr(sx, "STTOptions", None)
    if opts and dataclasses.is_dataclass(opts):
        for f in dataclasses.fields(opts):
            print(f"  {f.name:38s} default={f.default!r}")
    else:
        print(f"STTOptions = {opts!r}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("soniox: what it puts on the wire, and what it logs")
# The question this whole exercise turns on: is max_endpoint_delay_ms actually
# sent, and does the plugin distinguish final tokens from interim ones? Read it
# out of the installed source rather than trusting the docs.
try:
    from livekit.plugins import soniox as sx
    src_file = re.sub(r"__init__\.py$", "stt.py", sx.__file__)
    src = open(src_file, encoding="utf-8").read()
    print(f"(from {src_file})\n")
    wanted = ("endpoint", "is_final", "final", "logger.", "log.")
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if any(w in s for w in wanted) and not s.startswith("#"):
            print(f"  {i:5d}  {s[:150]}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")
