"""Is noise cancellation available to a self-hosted deployment at all?

Worth asking for a reason beyond audio quality. Call 342 waited 15926 ms because
Soniox would not accept that the caller had stopped, and a phone line carrying
constant low noise is the likeliest explanation - our own VAD ignored it, theirs
did not. Removing the noise would treat that at the source, which is better than
the ceiling that was written for it and withdrawn.

The caveat to settle first: livekit's own filter is a LiveKit Cloud feature and
this deployment is self-hosted. Read-only.

    /opt/aivoice/agent/.venv/bin/python /srv/aivoice/tools/probe-noise-cancellation.py
"""
from __future__ import annotations

import inspect


def head(t: str) -> None:
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


head("is the plugin even installed")
try:
    from livekit.plugins import noise_cancellation as nc
    print("yes:", nc.__file__)
    print("exports:", [n for n in dir(nc) if not n.startswith("_")])
except Exception as e:
    print(f"NOT INSTALLED - {type(e).__name__}: {e}")
    nc = None

if nc is not None:
    head("what each option says about itself")
    for name in [n for n in dir(nc) if not n.startswith("_")]:
        obj = getattr(nc, name)
        doc = (inspect.getdoc(obj) or "").strip()
        if doc or callable(obj):
            print(f"\n--- {name}")
            if doc:
                print("   " + "\n   ".join(doc.splitlines()[:12]))

    head("does it require livekit cloud - what the source says")
    try:
        import pathlib
        p = pathlib.Path(nc.__file__).parent
        for f in sorted(p.rglob("*.py")):
            for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                low = ln.lower()
                if any(w in low for w in ("cloud", "krisp", "only", "requires",
                                          "not supported", "self-host")):
                    print(f"  {f.name}:{i}  {ln.strip()[:130]}")
    except Exception as e:
        print(f"!! {type(e).__name__}: {e}")

head("where it would be plugged in")
try:
    from livekit.agents import RoomInputOptions
    sig = inspect.signature(RoomInputOptions.__init__)
    for p_ in sig.parameters.values():
        if p_.name != "self":
            print(f"  {p_.name:34s} = {p_.default!r}")
except Exception as e:
    print(f"!! {type(e).__name__}: {e}")
