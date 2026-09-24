"""Why the plugin gets no frames from our own TTS when curl gets 87 KB.

    ( set -a; . /opt/aivoice/.env; set +a
      cd /srv/aivoice && /opt/aivoice/agent/.venv/bin/python -u tools/probe-kokoro.py )

tts-bench reported "no audio frames were pushed" three times and then an
APIError, for both mp3 and pcm, while curl with the plugin's own parameters gets
a clean 200 and real audio from the same box. That leaves two possibilities and
they need different fixes:

    1. the SDK is not receiving the bytes     -> the request, or httpx
    2. the bytes arrive and produce no frames -> the emitter and its mime type

So this asks three questions in order, each one a layer further in:

    raw        the SDK's own streaming call, byte count only - no livekit
    plugin     voice_agent._build_tts, the object a call uses, frames counted
    traceback  whatever went wrong, in full, rather than a one-line warning

Prints KOKORO_URL. It is a LAN address, not a secret, and the first thing to
rule out is that it points somewhere other than intended.
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback

for _agent_dir in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"),
                   "/srv/aivoice/agent"):
    if os.path.isfile(os.path.join(_agent_dir, "store.py")):
        sys.path.insert(0, _agent_dir)
        break

TEXT = "Aapka naam kya hai, please?"


async def raw(url: str, fmt: str) -> None:
    """The SDK, with no livekit in the picture."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key="not-needed", base_url=url)
    try:
        got = 0
        chunks = 0
        async with client.audio.speech.with_streaming_response.create(
            input=TEXT, model="kokoro", voice="hf_alpha",
            response_format=fmt, speed=1.0, stream_format="audio",
        ) as stream:
            status = stream.status_code
            ctype = stream.headers.get("content-type")
            async for data in stream.iter_bytes():
                chunks += 1
                got += len(data)
        print(f"  raw {fmt:<4} http {status}  {ctype}  "
              f"{got} bytes in {chunks} chunks")
    except Exception:
        print(f"  raw {fmt:<4} FAILED")
        traceback.print_exc()
    finally:
        await client.close()


def _watch_emitter() -> dict:
    """Count what the plugin hands the emitter, from inside.

    Everything readable from outside agrees: the SDK gets 108 KB, Kokoro logs
    every attempt as 200, the mime type is recognised as raw PCM, request_id
    being empty is only a warning, and the framework calls end_input() and
    join() itself. And pushed_duration() is still 0.

    So the question is no longer what the server sent. It is whether push() is
    reached at all, and with how much - which nothing outside the emitter can
    answer.
    """
    from livekit.agents.tts import tts as lk_tts

    seen = {"initialize": 0, "push": 0, "bytes": 0, "flush": 0}
    E = lk_tts.AudioEmitter
    orig_init, orig_push, orig_flush = E.initialize, E.push, E.flush

    def initialize(self, **kw):
        seen["initialize"] += 1
        seen["args"] = {k: kw.get(k) for k in
                        ("sample_rate", "num_channels", "mime_type", "stream")}
        return orig_init(self, **kw)

    def push(self, data):
        seen["push"] += 1
        seen["bytes"] += len(data)
        return orig_push(self, data)

    def flush(self):
        seen["flush"] += 1
        return orig_flush(self)

    E.initialize, E.push, E.flush = initialize, push, flush
    return seen


async def through_plugin() -> None:
    """The object a call actually speaks through."""
    import dataclasses

    import store
    import voice_agent

    cfg = await store.load_config("default")
    pcfg = dataclasses.replace(cfg, tts_provider="kokoro",
                               tts_model="kokoro", tts_voice="hf_alpha")
    tts = voice_agent._build_tts("kokoro", pcfg, "", True)
    print(f"  plugin   streaming={tts.capabilities.streaming} "
          f"sample_rate={tts.sample_rate} "
          f"response_format={getattr(tts._opts, 'response_format', '?')}")
    seen = _watch_emitter()
    try:
        frames = 0
        samples = 0
        stream = tts.synthesize(TEXT)
        async for ev in stream:
            frames += 1
            samples += ev.frame.samples_per_channel
        print(f"  plugin   {frames} frames, "
              f"{samples / (tts.sample_rate or 24000):.2f}s of audio")
    except Exception:
        print("  plugin   FAILED")
        traceback.print_exc()
    finally:
        print(f"  emitter  initialize x{seen['initialize']}  "
              f"push x{seen['push']} ({seen['bytes']} bytes)  "
              f"flush x{seen['flush']}")
        print(f"  emitter  initialize args: {seen.get('args')}")


async def main() -> None:
    url = os.getenv("KOKORO_URL", "").strip()
    print(f"KOKORO_URL = {url or '(not set)'}")
    if not url:
        raise SystemExit("KOKORO_URL is not set in this environment")

    print("\nthe SDK on its own:")
    await raw(url, "pcm")
    await raw(url, "mp3")

    print("\nthrough the plugin a call uses:")
    await through_plugin()


if __name__ == "__main__":
    asyncio.run(main())
