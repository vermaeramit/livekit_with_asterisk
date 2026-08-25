"""What stt_node is handed, and what a speech event looks like.

Call 342: the caller trailed off with "लेकिन।" and Soniox took 15926 ms to send
its end token. The words were already with us the whole time - the plugin emits
interim transcripts continuously and only withholds the FINAL until Soniox
agrees the caller has stopped, which on a noisy phone line it may not do for
sixteen seconds.

So the plan is to stop waiting for that agreement: hold our own ceiling in
stt_node and promote the interim text ourselves when it expires. This prints the
exact shapes that has to be written against. Read-only.

    /opt/aivoice/agent/.venv/bin/python /srv/aivoice/tools/probe-stt-node.py
"""
from __future__ import annotations

import inspect


def head(t: str) -> None:
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


head("Agent.stt_node")
try:
    from livekit.agents import Agent
    print("signature:", inspect.signature(Agent.stt_node))
    print()
    print(inspect.getsource(Agent.stt_node))
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("the default implementation we must still be able to fall back to")
try:
    from livekit.agents.voice.agent import Agent as _A
    default = getattr(_A, "default", None)
    fn = getattr(default, "stt_node", None)
    print(inspect.getsource(fn) if fn else f"Agent.default = {default!r}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("speech events")
try:
    from livekit.agents import stt as lk_stt
    et = getattr(lk_stt, "SpeechEventType", None)
    print("SpeechEventType:", [m for m in dir(et) if m.isupper()] if et else "MISSING")
    for name in ("SpeechEvent", "SpeechData"):
        cls = getattr(lk_stt, name, None)
        if cls is None:
            print(f"{name}: MISSING")
            continue
        print(f"\n--- {name}")
        try:
            print("  ", inspect.signature(cls.__init__))
        except (TypeError, ValueError):
            pass
        print("   fields:", list(getattr(cls, "__annotations__", {}) or {}))
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")
