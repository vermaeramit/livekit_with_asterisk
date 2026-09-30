#!/usr/bin/env python3
"""Where Gemini TTS's 2.4 seconds go, and whether streaming moves any of it.

    # on .243, key read from the database
    docker exec -i admin-api python - < server-configs/gemini-probe.py

    # from a laptop, key in a file - to see the same thing over a different
    # network path, which is what found the Soniox India region
    python server-configs/gemini-probe.py --key-file C:\\Users\\verma\\gemini.key

The bench measured `first` against text length on 30 Sep 2026 and the fit was
flat:

    17 chars  2524 ms        first ~= 2400 ms + 7.3 ms per character
    27 chars  2637 ms
    90 chars  3019 ms
    95 chars  3097 ms

So the wait is not the speech being made - a 1.6 s clip costs the same as an
8.7 s one. It is a fixed cost paid before a single byte arrives, and
`generateContent` is the reason: it answers with ONE complete JSON body, so
nothing can be received until the whole clip has been generated, base64'd and
sent.

THE QUESTION THIS ANSWERS. `streamGenerateContent` exists. For text it emits
chunks. Whether it emits PARTIAL AUDIO for the TTS models is not documented
anywhere this project trusts, and guessing either way has cost a day before.
If it does, most of that fixed 2.4 s overlaps and the first word comes early.
If it does not - if the audio arrives in one chunk at the end regardless - then
Gemini TTS has a floor of ~2.4 s on this endpoint and no amount of our code
changes it.

WHAT IS SEPARATED, and why each matters:

    connect   TCP + TLS to the host. Distance, nothing else. The same question
              llm-net.py asks: is the work slow, or is it far away?
    ttfb      first byte of the HTTP response - headers, not audio
    audio     first byte of actual AUDIO. On generateContent this is ttfb plus
              parsing; on the streaming call it is the number that decides this
    total     last byte

Never prints a key.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

HOST = "generativelanguage.googleapis.com"
BASE = f"https://{HOST}/v1beta"
MODEL = "gemini-3.8-flash-tts"
VOICE = "Kore"

# The bench's own two extremes, so these numbers sit beside those directly.
SHORT = "आपका नाम क्या है?"
LONG = ("Splendor Plus ke baare mein aapko kya jankari chahiye? "
        "Price, features, finance, ya kuch aur?")

_RATE = re.compile(r"rate=(\d+)")


def payload(text: str) -> bytes:
    return json.dumps({
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICE}}},
        },
    }).encode()


def tls_connect_ms() -> float:
    """TCP + TLS only, to nothing. Distance with the work taken out."""
    t0 = time.monotonic()
    ctx = ssl.create_default_context()
    with socket.create_connection((HOST, 443), timeout=15) as raw:
        with ctx.wrap_socket(raw, server_hostname=HOST):
            return (time.monotonic() - t0) * 1000


def audio_of(obj: dict) -> tuple[bytes, int] | None:
    for cand in obj.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                mime = inline.get("mimeType") or inline.get("mime_type") or ""
                m = _RATE.search(mime)
                return base64.b64decode(inline["data"]), int(m.group(1)) if m else 24000
    return None


def request(url: str, key: str, text: str):
    req = urllib.request.Request(
        url, data=payload(text), method="POST",
        headers={"x-goog-api-key": key, "Content-Type": "application/json",
                 "User-Agent": "AIVoice-Probe/1.0"})
    return urllib.request.urlopen(req, timeout=60)


def once(url: str, key: str, text: str, streaming: bool) -> dict:
    t0 = time.monotonic()
    first_audio = None
    pcm, rate = b"", 24000
    try:
        r = request(url, key, text)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        return {"error": f"HTTP {e.code}: {body}"}
    with r:
        ttfb = (time.monotonic() - t0) * 1000
        if streaming:
            # alt=sse gives "data: {json}" per chunk. Read line by line so the
            # clock stops at the first chunk that actually carries audio - the
            # whole point of the exercise.
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    obj = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                got = audio_of(obj)
                if got:
                    if first_audio is None:
                        first_audio = (time.monotonic() - t0) * 1000
                    pcm += got[0]
                    rate = got[1]
        else:
            got = audio_of(json.loads(r.read().decode("utf-8", "replace")))
            if got:
                first_audio = (time.monotonic() - t0) * 1000
                pcm, rate = got
    return {
        "ttfb_ms": round(ttfb),
        "audio_ms": round(first_audio) if first_audio else None,
        "total_ms": round((time.monotonic() - t0) * 1000),
        "seconds": round(len(pcm) / (rate * 2), 2),
    }


def fetch_key_from_db() -> str:
    """The provider-catalog path: inside admin-api, decrypted here and nowhere else."""
    import asyncio
    import os
    sys.path.insert(0, "/app/kblib")
    import asyncpg
    import crypto

    async def go():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            row = await conn.fetchrow(
                "SELECT key_enc, key_hint FROM provider_keys WHERE provider = 'gemini' "
                "ORDER BY campaign_id NULLS FIRST, id LIMIT 1")
        finally:
            await conn.close()
        if row is None:
            raise SystemExit("no gemini key stored - add one in the console first")
        print(f"using the gemini key ending ····{row['key_hint']}\n")
        return crypto.decrypt(row["key_enc"])

    return asyncio.run(go())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--key-file", help="file whose FIRST LINE is the key")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    if args.key_file:
        with open(args.key_file, encoding="utf-8") as f:
            key = f.readline().strip()
        if not key:
            raise SystemExit(f"{args.key_file} is empty")
        print(f"using the key in {args.key_file} (····{key[-4:]})\n")
    else:
        key = fetch_key_from_db()

    print(f"model {args.model}")
    c = [tls_connect_ms() for _ in range(3)]
    print(f"TCP+TLS to {HOST}: {min(c):.0f}/{sum(c)/len(c):.0f}/{max(c):.0f} ms "
          f"(min/avg/max of 3) - distance, with no work in it\n")

    calls = [
        ("generateContent", f"{BASE}/models/{args.model}:generateContent", False),
        ("streamGenerateContent",
         f"{BASE}/models/{args.model}:streamGenerateContent?alt=sse", True),
    ]
    print(f"{'endpoint':<22} {'text':<6} {'ttfb':>7} {'1st audio':>10} "
          f"{'total':>7} {'audio':>7}")
    for name, url, streaming in calls:
        for label, text in (("short", SHORT), ("long", LONG)):
            for _ in range(args.runs):
                r = once(url, key, text, streaming)
                if "error" in r:
                    print(f"{name:<22} {label:<6} {r['error']}")
                    continue
                print(f"{name:<22} {label:<6} {r['ttfb_ms']:>6}ms "
                      f"{str(r['audio_ms']) + 'ms':>10} {r['total_ms']:>6}ms "
                      f"{r['seconds']:>6.2f}s")

    print("\nRead the '1st audio' column. If streaming is much lower than "
          "generateContent,\nthe fixed cost was waiting for the whole clip and "
          "we can take most of it back.\nIf the two match, ~2.4 s is the floor "
          "on this API and no code of ours moves it.")


main()
