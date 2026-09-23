"""Test a campaign's TTS on its own, outside a call.

    ( set -a; . /opt/aivoice/.env; set +a
      cd /srv/aivoice && /opt/aivoice/agent/.venv/bin/python server-configs/tts-bench.py default )

Built after call 622 (22 Sep 2026). There, the model's text had fully arrived
2.7 s before the first audio, and Soniox still delivered 1.5 s of speech with
6.0 s of stalls - each stall matching a hole in the recording to within 50 ms.
That places the breaking voice between the agent and Soniox's servers, but not
which of the three: the plugin, the network, or Soniox itself. This takes the
call away and asks the TTS directly.

Same key, same plugin, same settings as a call. The config comes from
store.load_config, the keys from store.load_provider_keys, and the TTS is built
by voice_agent._build_tts - the function a call uses. What is left out is
everything else: LiveKit, the room, Asterisk, the LLM.

Per synthesis it prints:

    first   request to the first audio frame
    audio   seconds of speech received
    wall    request to the last frame
    stalls  playback modelled from the first frame in real time, exactly as
            tts_node does in a call: a frame that arrives after the audio
            already received has run out is a stall of that length - the
            holes a caller hears

Two rounds per provider: one synthesis at a time, then two at once on the same
TTS object. Soniox shares one websocket between every stream of a TTS, and a
call does run two at once - the next answer is synthesised while the current
one plays, which is what call 622 was doing when its answers broke.

Each sentence is sent in Roman script and in Devanagari, so the script the model
chose can be ruled in or out rather than argued about.

A third round sends the campaign's own opening - the greeting, the recording
disclosure, and the two joined as a call joins them - each on its own, so a
provider that refuses one of them says which. Call 625 on Sarvam refused the
opening and the caller heard nothing for 10.6 s.

Never prints a key.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import sys
import time

# Beside this file when it is run from the checkout, and the checkout itself when
# it is not - it gets copied to /tmp to run on a box whose checkout is older.
for _agent_dir in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"),
                   "/srv/aivoice/agent"):
    if os.path.isfile(os.path.join(_agent_dir, "store.py")):
        sys.path.insert(0, _agent_dir)
        break

# Two sentences an agent actually said on the broken calls, and the same two in
# Devanagari.
TEXTS = [
    "Aapka naam kya hai, please?",
    "आपका नाम क्या है?",
    "Splendor Plus ke baare mein aapko kya jankari chahiye? Price, features, finance, ya kuch aur?",
    "स्प्लेंडर प्लस के बारे में आपको क्या जानकारी चाहिए? प्राइस, फीचर्स, फाइनेंस, या कुछ और?",
]

# What calls 621 and 622 ran. Used for a provider that is not the campaign's
# current one, so testing Soniox does not require switching the campaign back.
KNOWN = {
    "soniox": ("tts-rt-v2", "Kavya"),
    "sarvam": ("bulbul:v3", "kavya"),
}


async def measure(tts, text: str) -> dict:
    t0 = time.monotonic()
    first = run_out = None
    audio = 0.0
    stalls: list[float] = []
    async with tts.stream() as stream:
        stream.push_text(text)
        stream.end_input()
        async for ev in stream:
            now = time.monotonic()
            if first is None:
                first = run_out = now
            elif now > run_out + 0.02:
                stalls.append(now - run_out)
                run_out = now
            run_out += ev.frame.duration
            audio += ev.frame.duration
    return {
        "first_ms": int(((first or time.monotonic()) - t0) * 1000),
        "audio": audio,
        "wall": time.monotonic() - t0,
        "stalls": len(stalls),
        "stalled": sum(stalls),
        "worst_ms": int(max(stalls, default=0) * 1000),
    }


def short(text: str) -> str:
    return (text[:34] + "…") if len(text) > 35 else text


def line(provider: str, mode: str, text: str, r: dict) -> str:
    label = short(text)
    return (f"{provider:<7} {mode:<10} {label:<36} first {r['first_ms']:>5}ms  "
            f"audio {r['audio']:4.1f}s  wall {r['wall']:4.1f}s  "
            f"stalls {r['stalls']:>2}  stalled {r['stalled']:4.1f}s  worst {r['worst_ms']:>5}ms")


async def bench(provider: str, cfg, keys: dict, runs: int, quick: bool = False,
                regions: dict | None = None) -> None:
    import voice_agent

    if provider == cfg.tts_provider:
        pcfg = cfg
    else:
        model, voice = KNOWN[provider]
        pcfg = dataclasses.replace(cfg, tts_provider=provider, tts_model=model, tts_voice=voice)
    if provider not in keys:
        print(f"{provider}: no key for this campaign - skipped")
        return

    from livekit.agents import tokenize, tts as lk_tts

    # The key's own region, or the bench measures a host this campaign
    # never speaks to - which is the one thing a bench must not do.
    tts = voice_agent._build_tts(provider, pcfg, keys[provider], True,
                                 (regions or {}).get(provider))
    target = tts
    if not tts.capabilities.streaming:
        # Exactly what a call does with a TTS that cannot stream - see
        # Agent.default.tts_node in livekit-agents 1.6.7.
        target = lk_tts.StreamAdapter(
            tts=tts,
            sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(retain_format=True))
    print(f"\n{provider}  model={pcfg.tts_model}  voice={pcfg.tts_voice}  "
          f"language={pcfg.language}  sample_rate={tts.sample_rate}  "
          f"streaming={tts.capabilities.streaming}")
    print("(the first line includes opening the connection)")
    total_audio = total_stalled = 0.0
    try:
        if quick:
            # One long sentence, alone, nothing else: for sampling over time
            # without paying for the whole set every minute.
            for _ in range(runs):
                r = await measure(target, TEXTS[2])
                total_audio += r["audio"]
                total_stalled += r["stalled"]
                print(line(provider, "quick", TEXTS[2], r))
            print(f"{provider} total: {total_audio:.1f}s of audio, "
                  f"{total_stalled:.1f}s stalled")
            return
        for _ in range(runs):
            for text in TEXTS:
                r = await measure(target, text)
                total_audio += r["audio"]
                total_stalled += r["stalled"]
                print(line(provider, "alone", text, r))
        # Two at once on the same TTS - the way a call does it.
        for _ in range(runs):
            a, b = TEXTS[2], TEXTS[0]
            ra, rb = await asyncio.gather(measure(target, a), measure(target, b))
            for text, r in ((a, ra), (b, rb)):
                total_audio += r["audio"]
                total_stalled += r["stalled"]
                print(line(provider, "pair", text, r))

        # The campaign's own opening, in its parts. Call 625: Sarvam refused the
        # greeting four times - "Text must contain at least one character from
        # the allowed languages" - and the caller heard 10.6 s of nothing. The
        # opening is greeting + " " + recording_disclosure, and which part it
        # refused was not logged; each is sent on its own here to find out.
        whole = " ".join(x for x in (cfg.greeting, cfg.recording_disclosure) if x)
        for part, text in (("greeting", cfg.greeting or ""),
                           ("disclosure", cfg.recording_disclosure or ""),
                           ("opening", whole)):
            if not text.strip():
                continue
            try:
                print(line(provider, part, text, await measure(target, text)))
            except Exception as e:
                # The provider's own message, as the worker log already shows
                # it. It carries a request id, never the key, which travels in
                # a header.
                print(f"{provider:<7} {part:<10} {short(text):<36} FAILED "
                      f"{type(e).__name__}: {str(e)[:140]}")
        if "{" in (cfg.greeting or ""):
            print("  (the greeting has placeholders; they were sent unfilled)")
    finally:
        await tts.aclose()
    print(f"{provider} total: {total_audio:.1f}s of audio, {total_stalled:.1f}s stalled")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("campaign", help="agent_config name, e.g. default")
    ap.add_argument("--providers", default="soniox,sarvam")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--quick", action="store_true",
                    help="one long sentence only - for repeated sampling")
    # A call asks Soniox for 24 kHz PCM - 384 kbps - and the phone line keeps
    # 8 kHz of it. Asking for less is the test that separates the two remaining
    # suspects: fewer stalls at a lower rate means the bytes are the problem,
    # which is the network path; the same stalls mean Soniox is generating
    # slowly, however little is asked for.
    ap.add_argument("--sample-rate", type=int, default=None,
                    help="ask Soniox for this rate instead of the call's 24000")
    args = ap.parse_args()

    if args.sample_rate:
        import voice_agent
        voice_agent._TTS_NATIVE_RATE["soniox"] = args.sample_rate

    from livekit.agents.utils import http_context
    import store

    cfg = await store.load_config(args.campaign)
    keys = await store.load_provider_keys(cfg.campaign_id)
    regions = await store.load_provider_regions(cfg.campaign_id)
    print(f"campaign {cfg.name}: currently on {cfg.tts_provider}; "
          f"keys for {', '.join(sorted(keys)) or 'nothing'}")

    async with http_context.open():
        for provider in [p.strip() for p in args.providers.split(",") if p.strip()]:
            if provider not in KNOWN:
                print(f"{provider}: not a TTS this bench knows - skipped")
                continue
            try:
                await bench(provider, cfg, keys, args.runs, args.quick,
                            regions)
            except Exception as e:
                # The message only. A provider's error text can echo the request,
                # and the request carries the key.
                print(f"{provider}: FAILED - {type(e).__name__}")
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
