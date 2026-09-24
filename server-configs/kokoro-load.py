"""How many concurrent calls our own text-to-speech can carry.

    ( set -a; . /opt/aivoice/.env; set +a
      cd /srv/aivoice && /opt/aivoice/agent/.venv/bin/python -u server-configs/kokoro-load.py )

    kokoro-load.py --calls 5,10,20,40 --seconds 120

RUN IT FROM .243, not from the box: the network between the call server and the
GPU box is part of what a caller waits for, and measuring on the box itself
would leave it out.

WHY THIS IS NOT "FIRE N REQUESTS AT ONCE"

A call does not hold the TTS. It asks for speech when the agent answers - one
request per SENTENCE, because livekit wraps a non-streaming TTS in a
StreamAdapter - and then says nothing for ten or twenty seconds while the
caller talks and the model thinks. Twenty calls are not twenty requests in
flight; most of the time they are one or two. Firing N at once measures a
moment that does not happen and would send us to buy hardware for it.

So each simulated call here loops: think for a while, then synthesise an answer
of two or three sentences ONE AT A TIME, exactly as a real answer is rendered.
The concurrency is emergent, which is the point.

WHAT "PERFECTLY" MEANS, stated before the numbers rather than after

    first audio     what the caller hears as delay. Measured on real calls at
                    96-212 ms; Soniox on a good stretch was 400-900 ms
    faster than
    real time       a sentence must render in less wall time than it plays.
                    Once it does not, the caller hears a gap - and every call
                    behind it waits too

The report counts breaches of both, per concurrency level, so the answer is a
number of calls rather than an impression.

It speaks plain HTTP with the same payload agent/kokoro_tts.py sends. The box
cannot tell the difference, and what is being measured is the box.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import time

import aiohttp

# Two or three of these make one answer. Lengths are what the agent actually
# says - measured on call 652, where answers ran 11 to 202 characters.
SENTENCES = [
    "जी हाँ, बिल्कुल।",
    "अमित जी, आपकी फसल के लिए यह सही समय है।",
    "गेहूं की बुवाई के लिए चार क्विंटल बीज प्रति हेक्टेयर की दर से लगाना सही रहेगा।",
    "सिंचाई के लिए नियमित पानी देना और खेत की मिट्टी को नरम करने के लिए जुताई करना जरूरी है।",
    "क्या मैं आपको कल सुबह 10 बजे कॉल करूँ, या कोई और समय ठीक रहेगा?",
]

# What the caller and the model spend between two answers. A call turn on .243
# measured 1.3-1.7 s of wait plus however long the caller speaks; ten seconds
# is the conservative middle. Jittered so the simulated calls do not march in
# step, which would invent a burst that real traffic does not have.
THINK_S = 10.0
JITTER_S = 4.0

# The bar, from the measurements on real calls.
SLOW_MS = 400


async def say(session: aiohttp.ClientSession, url: str, voice: str,
              text: str) -> tuple[float, float, float]:
    """-> (ms to first audio, wall seconds, seconds of audio produced)."""
    t0 = time.monotonic()
    first = None
    got = 0
    async with session.post(
        f"{url}/audio/speech",
        json={"model": "kokoro", "voice": voice, "input": text,
              "response_format": "pcm", "speed": 1.0},
        timeout=aiohttp.ClientTimeout(total=120),
    ) as res:
        res.raise_for_status()
        async for chunk in res.content.iter_chunked(4096):
            if first is None:
                first = time.monotonic() - t0
            got += len(chunk)
    wall = time.monotonic() - t0
    # 16-bit mono at 24 kHz.
    return (first or wall) * 1000, wall, got / 2 / 24000


async def one_call(session, url, voice, stop_at: float, rows: list) -> None:
    """One simulated call: think, answer, think, answer."""
    # Staggered start, so twenty calls do not all answer in the same instant.
    await asyncio.sleep(random.uniform(0, THINK_S))
    while time.monotonic() < stop_at:
        for text in random.sample(SENTENCES, random.choice((2, 3))):
            if time.monotonic() >= stop_at:
                return
            try:
                rows.append(await say(session, url, voice, text))
            except Exception as e:
                rows.append((float("inf"), float("inf"), 0.0))
                print(f"    request failed: {type(e).__name__}: {str(e)[:80]}")
        await asyncio.sleep(random.uniform(THINK_S - JITTER_S, THINK_S + JITTER_S))


async def level(url: str, voice: str, calls: int, seconds: int) -> None:
    rows: list[tuple[float, float, float]] = []
    stop_at = time.monotonic() + seconds
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(one_call(session, url, voice, stop_at, rows)
                               for _ in range(calls)))

    ok = [r for r in rows if r[0] != float("inf")]
    if not ok:
        print(f"{calls:>6} {len(rows):>10}   every request failed")
        return
    firsts = sorted(r[0] for r in ok)
    p50 = statistics.median(firsts)
    p95 = firsts[min(len(firsts) - 1, int(len(firsts) * 0.95))]
    slow = sum(1 for f in firsts if f > SLOW_MS)
    # The one that matters more than any percentile: a sentence that took
    # longer to make than it takes to say.
    behind = sum(1 for _, wall, audio in ok if audio and wall > audio)
    failed = len(rows) - len(ok)
    print(f"{calls:>6} {len(rows):>10} {int(p50):>9}ms {int(p95):>7}ms "
          f"{int(firsts[-1]):>7}ms {slow:>11} {behind:>13} {failed:>8}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.getenv("KOKORO_URL", "").strip(),
                    help="default: KOKORO_URL from the environment")
    ap.add_argument("--voice", default="hf_alpha")
    ap.add_argument("--calls", default="5,10,20,40",
                    help="concurrency levels to try, in order")
    ap.add_argument("--seconds", type=int, default=90,
                    help="how long to hold each level")
    args = ap.parse_args()
    if not args.url:
        raise SystemExit("no --url and KOKORO_URL is not set")

    levels = [int(c) for c in args.calls.split(",") if c.strip()]
    print(f"{args.url}  voice {args.voice}  {args.seconds}s per level  "
          f"(a call answers every ~{THINK_S:.0f}s)\n")
    print(f"{'calls':>6} {'sentences':>10} {'first p50':>11} {'p95':>9} "
          f"{'worst':>9} {'over ' + str(SLOW_MS) + 'ms':>11} "
          f"{'behind real':>13} {'failed':>8}")
    for calls in levels:
        await level(args.url, args.voice, calls, args.seconds)

    print("\n'behind real' is the column that decides it: a sentence that took "
          "longer to render\nthan it takes to speak. Above zero, the box is "
          "the limit and every call behind it waits.")


if __name__ == "__main__":
    asyncio.run(main())
