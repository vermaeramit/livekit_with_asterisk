"""Ask Soniox for speech from ANY machine, and measure how it arrives.

    python soniox-probe.py                 # key from %USERPROFILE%\\soniox.key or ~/soniox.key
    python soniox-probe.py --key-file some.key --runs 3
    python soniox-probe.py --region in     # the India region, with an India key

Why this exists, when tts-bench.py already measures the same thing: the bench
needs the agent's config, keys and virtualenv, so it only runs on the two
servers - and both of them sit behind the same office network. On 23 Sep 2026
Soniox stalled on both, and the question left open was whether that is Soniox or
the path from this office. This has no dependency on the project at all: one
websocket, one API key in a file, and the same measurement. Run it from a laptop
on the office wifi, then from the same laptop on a phone hotspot, and the two
numbers answer it.

It speaks Soniox's websocket API directly, exactly as livekit-plugins-soniox
1.6.7 does: connect, send the config, push the text, then `text_end`, and read
base64 PCM back.

    first   request to the first audio
    audio   seconds of speech received
    wall    request to the last chunk
    stalls  playback modelled from the first chunk in real time - a chunk that
            arrives after the audio already received has run out is a stall of
            exactly that length. These are the holes a caller hears.

THE KEY IS NEVER PRINTED and never passed on the command line, where it would
land in shell history. Put it on the first line of a file, run this, then delete
the file.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
import uuid

import aiohttp

# Soniox runs the same API in four regions, and a key belongs to exactly one of
# them: a project picks its region at creation and gets region-specific keys.
# So the region here and the key file have to agree - a US key on the India
# host is a 401, not a slow answer.
REGIONS = ("us", "eu", "jp", "in")


def websocket_url(region: str) -> str:
    host = "tts-rt.soniox.com" if region == "us" else f"tts-rt.{region}.soniox.com"
    return f"wss://{host}/tts-websocket"

# What the calls run: the plugin's defaults for this project.
MODEL = "tts-rt-v2"
VOICE = "Kavya"
LANGUAGE = "hi"
SAMPLE_RATE = 24000
AUDIO_FORMAT = "pcm_s16le"

TEXTS = [
    "Aapka naam kya hai, please?",
    "आपका नाम क्या है?",
    "Splendor Plus ke baare mein aapko kya jankari chahiye? Price, features, finance, ya kuch aur?",
    "स्प्लेंडर प्लस के बारे में आपको क्या जानकारी चाहिए? प्राइस, फीचर्स, फाइनेंस, या कुछ और?",
]


def read_key(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            key = f.readline().strip()
    except OSError as e:
        raise SystemExit(
            f"cannot read {path}: {e.strerror}.\n"
            "Put the Soniox API key on the first line of that file, or pass "
            "--key-file. It is never printed and never goes on the command line.") from None
    if not key:
        raise SystemExit(f"{path} is empty - put the Soniox API key on its first line")
    return key


async def say(ws, key: str, text: str) -> dict:
    """One synthesis on an open connection. Returns the measurements."""
    stream_id = uuid.uuid4().hex[:12]
    t0 = time.monotonic()
    await ws.send_str(json.dumps({
        "api_key": key, "model": MODEL, "language": LANGUAGE, "voice": VOICE,
        "audio_format": AUDIO_FORMAT, "sample_rate": SAMPLE_RATE, "speed": 1.0,
        "stream_id": stream_id,
    }))
    await ws.send_str(json.dumps({"stream_id": stream_id, "text": text}))
    await ws.send_str(json.dumps({"stream_id": stream_id, "text_end": True}))

    first = run_out = None
    audio_s = 0.0
    stalls: list[float] = []
    async for msg in ws:
        if msg.type is not aiohttp.WSMsgType.TEXT:
            continue
        resp = json.loads(msg.data)
        if resp.get("stream_id") not in (stream_id, None):
            continue
        if resp.get("error_code"):
            raise SystemExit(f"Soniox said: {resp.get('error_code')} "
                             f"{resp.get('error_message')}")
        if chunk := resp.get("audio"):
            now = time.monotonic()
            # 16-bit mono: two bytes per sample.
            seconds = len(base64.b64decode(chunk)) / (SAMPLE_RATE * 2)
            if first is None:
                first = run_out = now
            elif now > run_out + 0.02:
                stalls.append(now - run_out)
                run_out = now
            run_out += seconds
            audio_s += seconds
        if resp.get("audio_end") or resp.get("terminated"):
            break

    return {
        "first_ms": int(((first or time.monotonic()) - t0) * 1000),
        "audio": audio_s,
        "wall": time.monotonic() - t0,
        "stalls": len(stalls),
        "stalled": sum(stalls),
        "worst_ms": int(max(stalls, default=0) * 1000),
    }


async def main() -> None:
    home = os.path.expanduser("~")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--key-file", default=os.path.join(home, "soniox.key"))
    ap.add_argument("--runs", type=int, default=2)
    # One long sentence instead of four, for watching over time without paying
    # for four sentences a minute. The long one is where stalls show.
    ap.add_argument("--quick", action="store_true",
                    help="one long sentence only - for repeated sampling")
    ap.add_argument("--region", choices=REGIONS, default="us",
                    help="which Soniox region to speak to - the key must have "
                         "been created in that region's project")
    args = ap.parse_args()
    texts = [TEXTS[2]] if args.quick else TEXTS
    url = websocket_url(args.region)

    key = read_key(args.key_file)
    print(f"{MODEL} / {VOICE} / {LANGUAGE} / {SAMPLE_RATE} Hz    "
          f"{args.region.upper()}    {time.strftime('%H:%M:%S')}")

    total_audio = total_stalled = 0.0
    async with aiohttp.ClientSession() as session:
        # One connection for every synthesis, which is what a call does: the
        # plugin shares one websocket across a TTS's streams.
        async with session.ws_connect(url) as ws:
            for _ in range(args.runs):
                for text in texts:
                    r = await say(ws, key, text)
                    total_audio += r["audio"]
                    total_stalled += r["stalled"]
                    label = (text[:34] + "…") if len(text) > 35 else text
                    print(f"{label:<36} first {r['first_ms']:>5}ms  audio {r['audio']:4.1f}s  "
                          f"wall {r['wall']:4.1f}s  stalls {r['stalls']:>2}  "
                          f"stalled {r['stalled']:4.1f}s  worst {r['worst_ms']:>5}ms")
    print(f"total: {total_audio:.1f}s of audio, {total_stalled:.1f}s stalled")


if __name__ == "__main__":
    asyncio.run(main())
