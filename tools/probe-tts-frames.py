"""What a TTS produces, and what say(audio=...) will accept.

The greeting is the same sentence on every call, in the same voice, and it costs
1.4s to synthesise on a good run and 6.8s on a bad one - after which two callers
today hung up before hearing anything. It should be rendered once and read from
disk, and say() already takes pre-rendered audio.

Before writing that: what synthesize() yields, what is on each chunk, and how an
AudioFrame is built back from raw bytes. Read-only.

    /opt/aivoice/agent/.venv/bin/python /srv/aivoice/tools/probe-tts-frames.py
"""
from __future__ import annotations

import inspect


def head(t: str) -> None:
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


head("TTS.synthesize / stream")
try:
    from livekit.agents import tts as agent_tts
    for name in ("TTS", "ChunkedStream", "SynthesizeStream", "SynthesizedAudio"):
        obj = getattr(agent_tts, name, None)
        if obj is None:
            print(f"{name}: MISSING")
            continue
        print(f"\n--- {name}")
        for meth in ("synthesize", "stream", "__anext__", "__init__"):
            fn = getattr(obj, meth, None)
            if fn is not None:
                try:
                    print(f"  {meth}{inspect.signature(fn)}")
                except (TypeError, ValueError):
                    print(f"  {meth}(?)")
        ann = list(getattr(obj, "__annotations__", {}) or {})
        if ann:
            print(f"  fields: {ann}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("rtc.AudioFrame - how to rebuild one from bytes")
try:
    from livekit import rtc
    print(f"__init__{inspect.signature(rtc.AudioFrame.__init__)}")
    print("attrs:", [a for a in dir(rtc.AudioFrame) if not a.startswith("_")])
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("what say() does with the audio it is given")
try:
    import pathlib

    import livekit.agents as la
    root = pathlib.Path(la.__file__).parent
    for rel in ("voice/agent_session.py", "voice/agent_activity.py"):
        p = root / rel
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        hits = [(i, ln) for i, ln in enumerate(lines, 1)
                if "audio" in ln and ("def say" in ln or "say(" in ln
                                      or "audio=" in ln or "AudioFrame" in ln)]
        if hits:
            print(f"\n--- {rel}")
            for i, ln in hits[:20]:
                print(f"  {i:5d}  {ln.strip()[:130]}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")
