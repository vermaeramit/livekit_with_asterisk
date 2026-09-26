"""Score a speech recogniser on the corpus, through the agent's own code.

    ( set -a; . /opt/aivoice/.env; set +a
      cd /srv/aivoice && /opt/aivoice/agent/.venv/bin/python -u server-configs/stt-bench.py default-test )

Build the corpus first with stt-corpus.py. This reads its manifest, feeds each
utterance to the STT the campaign actually uses - built by
voice_agent._build_stt, with the campaign's own key, model, language hints and
vocabulary - and compares what comes back with the words that were spoken.

    WER         word error rate: substitutions + deletions + insertions,
                over the number of words that were said. 0.10 is one word in
                ten wrong
    terms       the campaign's stt_context_terms - its product names - and how
                many survived. This is the number that ended a call once:
                "Splendor Plus Flex" arrived as "Lender Plus Flex" and matched
                a different bike at 0.57

TWO BIASES, both in the same direction, both worth knowing before reading the
numbers:

1. The audio is synthetic. No background noise, no accents, no false starts.
   Every recogniser scores better here than on a real call.

2. The reference text was written by SONIOX, on real calls, and then spoken
   back. So it carries Soniox's spelling conventions - English words in
   Devanagari, digits as digits - and a recogniser that writes "year end" in
   Latin or "दो" for "2" is marked wrong for a difference that is not an error.
   Normalisation below removes some of that and cannot remove all of it.

   Soniox therefore has a home advantage on this corpus. Read its score as a
   ceiling rather than a measurement, and compare the others to each other.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import unicodedata
import wave

for _agent_dir in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"),
                   "/srv/aivoice/agent"):
    if os.path.isfile(os.path.join(_agent_dir, "store.py")):
        sys.path.insert(0, _agent_dir)
        break

import store                                                     # noqa: E402
import voice_agent                                               # noqa: E402

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _is_word_char(ch: str) -> bool:
    """Letters, numbers, and COMBINING MARKS.

    The marks are the whole reason this is a function rather than a regex.
    `[^\\w\\s]` looks like "strip punctuation" and is not: Python's \\w matches
    what Unicode calls alphanumeric, and a matra or a virama is neither - it is
    category Mn. So that pattern quietly ate them, and "नमस्ते" came out as
    "नमस" and "त". Every Hindi word would have been torn into pieces and every
    error rate would have been fiction.
    """
    return unicodedata.category(ch)[0] in ("L", "N", "M")


def normalise(text: str) -> list[str]:
    """Words, stripped of everything that is not a word.

    Deliberately shallow. A deeper normaliser - mapping number words to digits,
    transliterating Latin to Devanagari - would start deciding what counts as
    correct, and the whole point of this bench is to measure rather than to
    decide.
    """
    t = unicodedata.normalize("NFKC", text).lower().translate(_DEVANAGARI_DIGITS)
    return "".join(c if _is_word_char(c) else " " for c in t).split()


def edits(ref: list[str], hyp: list[str]) -> tuple[int, int, int]:
    """-> (substitutions, deletions, insertions), by Levenshtein alignment."""
    n, m = len(ref), len(hyp)
    # (cost, sub, del, ins) per cell; one row at a time, since only the counts
    # are wanted and not the alignment itself.
    prev = [(j, 0, 0, j) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [(i, 0, i, 0)]
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                cur.append(prev[j - 1])
                continue
            sub = (prev[j - 1][0] + 1, prev[j - 1][1] + 1, prev[j - 1][2], prev[j - 1][3])
            dele = (prev[j][0] + 1, prev[j][1], prev[j][2] + 1, prev[j][3])
            ins = (cur[j - 1][0] + 1, cur[j - 1][1], cur[j - 1][2], cur[j - 1][3] + 1)
            cur.append(min(sub, dele, ins))
        prev = cur
    return prev[m][1], prev[m][2], prev[m][3]


def frames(path: str, ms: int = 20):
    """The wav as livekit audio frames, the size a call delivers them in."""
    from livekit import rtc

    with wave.open(path, "rb") as w:
        rate, chans = w.getframerate(), w.getnchannels()
        per = int(rate * ms / 1000)
        while True:
            data = w.readframes(per)
            if not data:
                return
            yield rtc.AudioFrame(
                data=data, sample_rate=rate, num_channels=chans,
                samples_per_channel=len(data) // 2 // chans,
            ), rate


async def transcribe(stt, path: str, pace: float) -> str:
    """-> everything the recogniser finally decided was said."""
    from livekit.agents import stt as lk_stt

    if not stt.capabilities.streaming:
        # A non-streaming recogniser has no stream() - a call gets one only
        # because livekit wraps it in a StreamAdapter with the session's VAD.
        # Here the wrapping would be theatre: every file in the corpus is
        # exactly one utterance, which is what the VAD would have cut out of a
        # call anyway. So ask it the one question it can answer.
        ev = await stt.recognize([f for f, _ in frames(path)])
        return ev.alternatives[0].text.strip() if ev.alternatives else ""

    stream = stt.stream()
    said: list[str] = []

    async def read() -> None:
        async for ev in stream:
            if ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT and ev.alternatives:
                text = ev.alternatives[0].text.strip()
                if text:
                    said.append(text)

    reader = asyncio.create_task(read())
    for frame, rate in frames(path):
        stream.push_frame(frame)
        if pace:
            # Real time, or a multiple of it. Some recognisers decide a turn
            # has ended from the CLOCK, not from the audio, and firing a whole
            # utterance at them in one burst is not what a call does.
            await asyncio.sleep(frame.samples_per_channel / rate / pace)
        else:
            await asyncio.sleep(0)
    stream.end_input()
    try:
        await asyncio.wait_for(reader, timeout=60)
    except asyncio.TimeoutError:
        reader.cancel()
    await stream.aclose()
    return " ".join(said)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("campaign", help="config name, e.g. default-test")
    ap.add_argument("--corpus", default="/tmp/stt-corpus")
    ap.add_argument("--provider", default="",
                    help="default: whatever the campaign is configured to use")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="0 = push as fast as it accepts; 1 = real time, 4 = "
                         "four times real time. Try 1 if transcripts come "
                         "back empty")
    ap.add_argument("--show", type=int, default=5,
                    help="how many of the worst utterances to print")
    args = ap.parse_args()

    manifest = os.path.join(args.corpus, "manifest.jsonl")
    with open(manifest, encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]
    if not items:
        raise SystemExit(f"{manifest} is empty - run stt-corpus.py first")

    cfg = await store.load_config(args.campaign)
    keys = await store.load_provider_keys(cfg.campaign_id)
    regions = await store.load_provider_regions(cfg.campaign_id)
    provider = args.provider or cfg.stt_provider

    terms = [t.lower() for t in (getattr(cfg, "stt_context_terms", None) or [])]
    print(f"campaign {cfg.name}: stt {provider}, model {cfg.stt_model}, "
          f"language {cfg.language}, {len(terms)} vocabulary terms")
    print(f"{len(items)} utterances from {args.corpus}\n")

    from livekit.agents import utils

    async with utils.http_context.open():
        stt = voice_agent._build_stt(provider, cfg, keys.get(provider, ""),
                                     True, regions.get(provider))
        rows = []
        t0 = time.monotonic()
        for i, item in enumerate(items):
            path = os.path.join(args.corpus, item["audio"])
            try:
                hyp = await transcribe(stt, path, args.pace)
            except Exception as e:
                print(f"  {item['audio']} FAILED {type(e).__name__}: {str(e)[:90]}")
                continue
            ref_w, hyp_w = normalise(item["text"]), normalise(hyp)
            s, d, ins = edits(ref_w, hyp_w)
            rows.append({"audio": item["audio"], "ref": item["text"], "hyp": hyp,
                         "n": len(ref_w), "err": s + d + ins,
                         "s": s, "d": d, "i": ins})
            print(f"\r  {i + 1}/{len(items)}", end="", flush=True)
        print(f"\r  done in {int(time.monotonic() - t0)}s" + " " * 20)

    if not rows:
        raise SystemExit("nothing transcribed - try --pace 1")

    words = sum(r["n"] for r in rows)
    errs = sum(r["err"] for r in rows)
    print(f"\n{provider}:  WER {errs / words:.3f}   "
          f"({errs} errors in {words} words: {sum(r['s'] for r in rows)} sub, "
          f"{sum(r['d'] for r in rows)} del, {sum(r['i'] for r in rows)} ins)")

    if terms:
        # Only terms that were actually spoken can be found. A term the corpus
        # never says tells us nothing about the recogniser.
        spoken = found = 0
        for r in rows:
            ref_l, hyp_l = r["ref"].lower(), r["hyp"].lower()
            for t in terms:
                if t in ref_l:
                    spoken += 1
                    found += t in hyp_l
        if spoken:
            print(f"{'':>{len(provider)}}   vocabulary: {found}/{spoken} "
                  f"of the campaign's terms survived ({found / spoken:.0%})")
        else:
            print(f"{'':>{len(provider)}}   vocabulary: none of the "
                  f"{len(terms)} terms appear in this corpus")

    rows.sort(key=lambda r: r["err"] / max(r["n"], 1), reverse=True)
    print(f"\nthe {min(args.show, len(rows))} worst:")
    for r in rows[:args.show]:
        print(f"\n  {r['audio']}  {r['err']}/{r['n']} wrong")
        print(f"    said:  {r['ref']}")
        print(f"    heard: {r['hyp'] or '(nothing)'}")

    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
