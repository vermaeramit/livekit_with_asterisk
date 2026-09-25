"""Build a speech corpus whose right answer is already known.

    ( set -a; . /opt/aivoice/.env; set +a
      cd /srv/aivoice && /opt/aivoice/agent/.venv/bin/python -u server-configs/stt-corpus.py default-test )

WHY THIS AND NOT REAL CALL AUDIO

Judging a speech recogniser needs audio AND the words that were actually said.
We have hours of real calls, and none of that: `MixMonitor` writes both sides
into one file, so the caller cannot be separated from the agent, and the only
transcript we hold is the one the CURRENT recogniser produced - which is the
thing under test. Getting real caller-only audio means changing the dialplan on
a system that is working, and getting true transcripts means somebody sitting
down and typing out half an hour of speech.

So this goes the other way round. It takes sentences REAL CALLERS ACTUALLY SAID,
from the turns table - their words, their product names, their numbers - and has
our own TTS speak them. The right answer is then free and exact, because we
supplied it.

The method is not invented here: it is what the TTS-STT Flywheel paper does
(arXiv 2605.03073), for the reason that entity-dense Indic audio with reliable
labels does not otherwise exist.

WHAT IT CANNOT TELL YOU, said before the numbers

Synthetic speech is not a caller. No background noise, no accent variety, no
false starts, no talking over the agent. Every recogniser will score BETTER
here than on a real call, so the absolute WER is optimistic and should never be
quoted as "our accuracy".

What it is good for is comparison - every candidate gets the same audio - and
as a cheap filter: a model that loses product names on clean synthetic speech
will not find them on a phone line.

THE CHANNEL is simulated rather than assumed. Kokoro serves 24 kHz; a phone
call is 8 kHz and everything above 4 kHz is simply gone. So the audio is
band-limited to 8 kHz and then resampled to the 16 kHz that recognisers expect,
which is the same journey a caller's voice makes.
"""
from __future__ import annotations

import argparse
import asyncio
# Deprecated in 3.12 and gone in 3.13. The servers are on 3.12 and this is a
# bench, not the call path, so it stays until one of them moves - at which
# point `audioop-lts` is a drop-in, or ratecv's job is forty lines of filter
# and decimation. Writing those forty lines today would be guessing at which
# fix is needed.
import audioop
import json
import os
import sys
import wave

import aiohttp

for _agent_dir in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"),
                   "/srv/aivoice/agent"):
    if os.path.isfile(os.path.join(_agent_dir, "store.py")):
        sys.path.insert(0, _agent_dir)
        break

import store                                                     # noqa: E402

KOKORO_RATE = 24000
PHONE_RATE = 8000
ASR_RATE = 16000


async def caller_lines(config_name: str, limit: int, min_chars: int,
                       max_chars: int) -> list[str]:
    """Distinct things callers said on this campaign, longest first.

    Longest first on purpose: a one-word "haan" scores 0% or 100% and measures
    nothing. The sentences worth testing are the ones carrying a product name,
    a quantity or a place.
    """
    # length() is in the select list because it is in the ORDER BY and the
    # query is DISTINCT - postgres refuses to sort a distinct result by an
    # expression it did not return.
    rows = await (await store.pool()).fetch(
        """SELECT DISTINCT t.text, length(t.text) AS n
             FROM turns t
             JOIN calls c ON c.id = t.call_id
            WHERE c.config_name = $1
              AND t.role = 'user'
              AND t.text IS NOT NULL
              AND length(btrim(t.text)) BETWEEN $2 AND $3
            ORDER BY n DESC
            LIMIT $4""",
        config_name, min_chars, max_chars, limit,
    )
    return [" ".join(r["text"].split()) for r in rows]


async def speak(session: aiohttp.ClientSession, url: str, voice: str,
                text: str) -> bytes:
    """-> raw 16-bit PCM at 24 kHz, as Kokoro serves it."""
    async with session.post(
        f"{url}/audio/speech",
        json={"model": "kokoro", "voice": voice, "input": text,
              "response_format": "pcm", "speed": 1.0},
        timeout=aiohttp.ClientTimeout(total=120),
    ) as res:
        res.raise_for_status()
        return await res.read()


def down_and_up(pcm24: bytes) -> bytes:
    """24 kHz -> 8 kHz -> 16 kHz: the journey a caller's voice makes.

    Going down to 8 kHz throws away everything above 4 kHz, and coming back up
    does not return it. That loss is the whole point - it is what a phone line
    does, and it is what separates a recogniser that works on a podcast from
    one that works on a call.
    """
    phone, _ = audioop.ratecv(pcm24, 2, 1, KOKORO_RATE, PHONE_RATE, None)
    asr, _ = audioop.ratecv(phone, 2, 1, PHONE_RATE, ASR_RATE, None)
    return asr


def write_wav(path: str, pcm: bytes, rate: int) -> float:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return len(pcm) / 2 / rate


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("campaign", help="config name, e.g. default-test")
    ap.add_argument("--out", default="/tmp/stt-corpus")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--voice", default="hf_alpha",
                    help="repeat the run with hm_omega for a male voice")
    ap.add_argument("--min-chars", type=int, default=15)
    ap.add_argument("--max-chars", type=int, default=160)
    ap.add_argument("--url", default=os.getenv("KOKORO_URL", "").strip())
    args = ap.parse_args()
    if not args.url:
        raise SystemExit("KOKORO_URL is not set and --url was not given")

    lines = await caller_lines(args.campaign, args.limit,
                               args.min_chars, args.max_chars)
    if not lines:
        raise SystemExit(f"no caller turns found for campaign '{args.campaign}'")

    os.makedirs(args.out, exist_ok=True)
    manifest = os.path.join(args.out, "manifest.jsonl")
    total = 0.0
    written = 0
    async with aiohttp.ClientSession() as session:
        with open(manifest, "w", encoding="utf-8") as mf:
            for i, text in enumerate(lines):
                try:
                    pcm = await speak(session, args.url, args.voice, text)
                except Exception as e:
                    print(f"  {i:>3} FAILED {type(e).__name__}: {str(e)[:70]}")
                    continue
                if len(pcm) < 4000:
                    # Under a tenth of a second. Kokoro makes no audio for a
                    # string with nothing to say in it - a stray "." did
                    # exactly this on the tts bench.
                    print(f"  {i:>3} skipped, no audio: {text[:40]!r}")
                    continue
                name = f"{i:03d}.wav"
                secs = write_wav(os.path.join(args.out, name),
                                 down_and_up(pcm), ASR_RATE)
                mf.write(json.dumps({"audio": name, "text": text,
                                     "seconds": round(secs, 2),
                                     "voice": args.voice},
                                    ensure_ascii=False) + "\n")
                total += secs
                written += 1

    print(f"\n{written} utterances, {total:.0f}s of audio, {args.voice}")
    print(f"written to {args.out} with manifest.jsonl")
    print("\nThese are real caller sentences spoken by our own TTS and put "
          "through an 8 kHz\nchannel. The text in the manifest is the right "
          "answer, exactly. Synthetic speech\nflatters every recogniser, so "
          "use the numbers to compare candidates - not to quote\nas this "
          "system's accuracy.")
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
