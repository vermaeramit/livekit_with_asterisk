"""Find out what actually governs how long a turn waits.

The soniox plugin only emits a FINAL transcript when Soniox sends an end token.
We have measured 6684ms between the caller falling silent and that token, with
no reconnects and the words themselves already accumulated inside the plugin.

So the question is not "how do we make Soniox faster" but "does livekit have to
wait for it at all". This prints the session's own endpointing knobs and the
code that decides when a turn is committed. Read-only.

    /opt/aivoice/agent/.venv/bin/python /srv/aivoice/tools/probe-endpointing.py
"""
from __future__ import annotations

import inspect
import pathlib
import re


def head(t: str) -> None:
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


head("AgentSession(...) - every knob it accepts")
try:
    from livekit.agents import AgentSession
    for p in inspect.signature(AgentSession.__init__).parameters.values():
        if p.name != "self":
            print(f"  {p.name:34s} = {p.default!r}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("where these numbers come from")
try:
    import livekit.agents as la
    root = pathlib.Path(la.__file__).parent

    # The three names that decide the wait, wherever they are used.
    terms = ("max_endpointing_delay", "min_endpointing_delay",
             "transcription_delay", "end_of_utterance_delay")
    for f in sorted(root.rglob("*.py")):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        hits = [(i, ln) for i, ln in enumerate(lines, 1)
                if any(t in ln for t in terms)]
        if not hits:
            continue
        print(f"\n--- {f.relative_to(root)}")
        for i, ln in hits:
            print(f"  {i:5d}  {ln.strip()[:140]}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")

head("what happens while a final transcript has not arrived")
try:
    import livekit.agents as la
    p = pathlib.Path(la.__file__).parent / "voice" / "audio_recognition.py"
    src = p.read_text(encoding="utf-8")
    print(f"(from {p})")
    # The commit path: whichever function decides the user's turn is over.
    for m in re.finditer(r"^\s*(async )?def .*(endpoint|turn|commit|final).*:",
                         src, re.M | re.I):
        line = src[:m.start()].count("\n") + 1
        print(f"  {line:5d}  {m.group(0).strip()}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")
