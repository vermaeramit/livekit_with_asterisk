"""The last question: what arms the endpointing timer, and what replaced it.

Two things are now established. transcription_delay is measured from VAD silence
to the final transcript, and min_endpointing_delay's own docstring says the
endpointing timers run AFTER the STT's end-of-speech signal - "additive with the
STT provider's endpointing delay". If max_delay is armed the same way, our 1.5s
ceiling was never a ceiling at all, and no Soniox knob changes that.

Also: min/max_endpointing_delay are deprecated in 1.6.7 in favour of
turn_handling=TurnHandlingOptions(...), which we have never looked at. Read-only.

    /opt/aivoice/agent/.venv/bin/python /srv/aivoice/tools/probe-turn-handling.py
"""
from __future__ import annotations

import inspect
import pathlib

import livekit.agents as la

ROOT = pathlib.Path(la.__file__).parent


def head(t: str) -> None:
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


head("what the timer is armed from")
# The whole question in one grep: is the deadline computed from when the caller
# fell silent, or from a signal the STT has to send us first?
p = ROOT / "voice" / "audio_recognition.py"
try:
    lines = p.read_text(encoding="utf-8").splitlines()
    terms = ("max_delay", "min_delay", "END_OF_SPEECH", "end_of_speech",
             "last_speaking_time", "_commit_user_turn", "call_later",
             "_endpointing", "deadline")
    for i, ln in enumerate(lines, 1):
        if any(t in ln for t in terms):
            print(f"  {i:5d}  {ln.rstrip()[:150]}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("TurnHandlingOptions - the API that replaced it")
try:
    obj = None
    for mod in ("livekit.agents", "livekit.agents.voice",
                "livekit.agents.voice.agent_session", "livekit.agents.voice.turn"):
        try:
            m = __import__(mod, fromlist=["TurnHandlingOptions"])
            obj = getattr(m, "TurnHandlingOptions", None)
            if obj is not None:
                print(f"(found in {mod})")
                break
        except Exception:
            continue
    print(inspect.getsource(obj) if obj is not None else "NOT FOUND")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("preemptive_generation - what it actually does")
try:
    src = (ROOT / "voice" / "agent_session.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    for i, ln in enumerate(lines, 1):
        if "preemptive" in ln.lower():
            print(f"  {i:5d}  {ln.rstrip()[:150]}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")
