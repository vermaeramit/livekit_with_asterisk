#!/usr/bin/env python3
"""Measure Raya's text-to-speech before anything in this repo depends on it.

    python server-configs/raya-probe.py --key-file C:\\Users\\verma\\raya.key

Two requests, one short sentence each, because the account has a small balance
and the question does not need more than that. The same sentence is sent twice -
once at 24 kHz, once at 8 kHz - so the two numbers are comparable to each other
and to everything already measured here:

    google Chirp3-HD   154 ms on a live call, 304 ms on the bench
    soniox (India)     ~250 ms
    sarvam             ~240 ms
    gemini 3.8         ~2000 ms
    kokoro (our LAN)   113-162 ms

WHY 8 kHz IS ASKED FOR AT ALL. The trunk carries 8 kHz and everything above
4 kHz is discarded on the way to the caller, so three quarters of a 24 kHz
render is built, sent and thrown away. Raya is the first provider offering both
8 kHz and mulaw on the streaming endpoint; whether that actually arrives sooner
is a measurement, not an assumption.

WHAT ELSE THIS CHECKS. The docs say the chunks are "base64-encoded PCM F32LE" -
float32. Every other provider in this system returns signed 16-bit, which is
what livekit's AudioEmitter slices into frames. A float stream read as int16 is
not an error, it is noise, so the sample width is checked here rather than
discovered on a call.

Never prints the key.
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import ssl
import struct
import time
import urllib.error
import urllib.request

HOST = "hub.getraya.app"
BASE = f"https://{HOST}/v1"

# The plugin's own defaults, so this measures what an integration would get.
VOICE_ID = "3fe4afbc-3bde-4c97-ab8e-37e3fb8c7ba2"
MODEL = "m1"
LANGUAGE = "hi"

# One short line. Deliberately not the long bench sentence - credits are
# limited and time-to-FIRST-audio is what is being measured, which the length
# barely touches on a streaming provider.
TEXT = "नमस्ते, मैं आपकी क्या मदद कर सकती हूँ?"


def tls_connect_ms() -> float:
    t0 = time.monotonic()
    ctx = ssl.create_default_context()
    with socket.create_connection((HOST, 443), timeout=15) as raw:
        with ctx.wrap_socket(raw, server_hostname=HOST):
            return (time.monotonic() - t0) * 1000


def looks_like_f32(pcm: bytes) -> str:
    """-> what these bytes most plausibly are.

    float32 audio sits in [-1, 1]; the same bytes read as int16 would be a
    spray of huge and tiny values. Reading the first few hundred samples both
    ways separates them without needing the provider to be believed.
    """
    if len(pcm) < 400 or len(pcm) % 4:
        return f"{len(pcm)} bytes, not a whole number of float32 samples"
    floats = struct.unpack(f"<{len(pcm) // 4}f", pcm[: (len(pcm) // 4) * 4])
    sample = floats[:500]
    in_range = sum(1 for f in sample if -1.0 <= f <= 1.0)
    return (f"float32: {in_range}/{len(sample)} of the first samples are within "
            f"[-1,1], peak {max(abs(f) for f in sample):.3f}")


def run(key: str, rate: int, codec: str = "pcm") -> None:
    body = json.dumps({
        "text": TEXT,
        "voice_id": VOICE_ID,
        "language": LANGUAGE,
        "model": MODEL,
        "codec": codec,
        "sample_rate": rate,
        "speed": 1.0,
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/text-to-speech/stream", data=body, method="POST",
        # The streaming endpoint's OpenAPI spec lists no security, while the
        # one-shot endpoint takes X-API-Key. Sent either way: a header that is
        # ignored costs nothing, and a missing one costs a request.
        headers={"X-API-Key": key, "Content-Type": "application/json",
                 "Accept": "text/event-stream",
                 "User-Agent": "AIVoice-Probe/1.0"})

    t0 = time.monotonic()
    first_audio = None
    chunks = 0
    step_times: list[float] = []
    pcm = b""
    try:
        r = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
        return
    with r:
        ttfb = (time.monotonic() - t0) * 1000
        ctype = r.headers.get("Content-Type", "")
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            data = obj.get("data")
            if not data:
                continue
            if first_audio is None:
                first_audio = (time.monotonic() - t0) * 1000
            chunks += 1
            if obj.get("step_time") is not None:
                step_times.append(float(obj["step_time"]))
            pcm += base64.b64decode(data)
    total = (time.monotonic() - t0) * 1000

    print(f"  content-type  {ctype}")
    print(f"  ttfb          {ttfb:7.0f} ms")
    print(f"  FIRST AUDIO   {first_audio if first_audio is None else round(first_audio):>7} ms")
    print(f"  total         {total:7.0f} ms   chunks {chunks}")
    if step_times:
        print(f"  step_time     first {step_times[0]:.3f}s  "
              f"max {max(step_times):.3f}s  sum {sum(step_times):.3f}s")
    if pcm:
        secs32 = len(pcm) / 4 / rate
        secs16 = len(pcm) / 2 / rate
        print(f"  audio bytes   {len(pcm)}  -> {secs32:.2f}s if float32, "
              f"{secs16:.2f}s if int16")
        print(f"  sample check  {looks_like_f32(pcm)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--key-file", required=True,
                    help="file whose FIRST LINE is the API key")
    ap.add_argument("--rates", default="24000,8000",
                    help="comma-separated; one request each")
    args = ap.parse_args()

    with open(args.key_file, encoding="utf-8") as f:
        key = f.readline().strip()
    if not key:
        raise SystemExit(f"{args.key_file} is empty")
    print(f"key from {args.key_file} (····{key[-4:]})")

    c = [tls_connect_ms() for _ in range(3)]
    print(f"TCP+TLS to {HOST}: {min(c):.0f}/{sum(c)/len(c):.0f}/{max(c):.0f} ms "
          f"(min/avg/max of 3) - distance, with no work in it")
    print(f"text: {TEXT}  ({len(TEXT)} chars)")

    for rate in [int(x) for x in args.rates.split(",") if x.strip()]:
        print(f"\n--- sample_rate {rate}, codec pcm ---")
        run(key, rate)


main()
