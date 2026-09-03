"""Synthesise a short line so somebody can hear a voice before choosing it.

Soniox offers 70 voices on tts-rt-v2. Picking one from a dropdown of names and
one-line descriptions is guessing, and the campaign only finds out what it
sounds like on a real call.

WHY THIS IS NOT THE AGENT'S CODE

The agent builds a `soniox.TTS` from the livekit plugin, which the admin image
does not have - those plugins live in the agent's venv. Adding them here would
pull livekit-agents and its dependency tree into this image for one preview
button.

So the provider is called directly. Soniox's TTS is a websocket, and `websockets`
is already installed as part of uvicorn[standard] - nothing new is needed. The
protocol below was read out of the installed plugin rather than remembered:

    ->  {"api_key", "model", "language", "voice", "audio_format",
         "sample_rate", "speed", "stream_id"}
    ->  {"stream_id", "text"}
    ->  {"stream_id", "text_end": true}
    <-  {"stream_id", "audio": "<base64>"}    repeated
    <-  {"stream_id", "audio_end": true}
    <-  {"stream_id", "terminated": true}
    <-  {"stream_id", "error_code", "error_message"}   on failure

That duplication is a real cost: if Soniox changes the protocol, the plugin gets
updated and this does not. It is bounded - a preview breaking is not a call
breaking - and it is the reason this file says exactly where the shapes came
from.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid

import websockets

log = logging.getLogger("admin-api")

SONIOX_WS = "wss://tts-rt.soniox.com/tts-websocket"

# mp3 so the browser can play the bytes as they are. PCM would mean sending a
# WAV header we assembled ourselves for no gain.
_FORMAT = "mp3"

# A preview is one short line. Anything longer is somebody using this as a
# free text-to-speech service, and it is billed to the campaign's own key.
MAX_CHARS = 400

# Generous, because a cold connection plus synthesis is not instant, and short
# enough that a hung provider does not hold a request open.
_TIMEOUT = 30


class PreviewError(Exception):
    """The provider refused, or said nothing. Carries no key material."""


async def soniox(api_key: str, *, model: str, voice: str, language: str,
                 text: str, speed: float = 1.0) -> bytes:
    """-> mp3 bytes."""
    stream_id = uuid.uuid4().hex
    config = {
        "api_key": api_key,
        "model": model,
        "language": language,
        "voice": voice,
        "audio_format": _FORMAT,
        "speed": speed,
        "stream_id": stream_id,
    }

    audio = bytearray()
    try:
        async with websockets.connect(SONIOX_WS, max_size=None) as ws:
            await ws.send(json.dumps(config))
            await ws.send(json.dumps({"stream_id": stream_id, "text": text}))
            await ws.send(json.dumps({"stream_id": stream_id, "text_end": True}))

            async def drain() -> None:
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("error_code"):
                        # The provider's own words, and nothing of ours: the
                        # config we sent it has the key in it.
                        raise PreviewError(
                            f"{msg.get('error_code')}: "
                            f"{msg.get('error_message', 'unknown error')}")
                    chunk = msg.get("audio")
                    if chunk:
                        audio.extend(base64.b64decode(chunk))
                    if msg.get("terminated"):
                        return

            await asyncio.wait_for(drain(), timeout=_TIMEOUT)
    except PreviewError:
        raise
    except asyncio.TimeoutError:
        raise PreviewError("the provider did not finish in time")
    except Exception as e:
        # Never the exception text: a connection error can quote the URL and
        # the handshake, and the config that went up it carried the key.
        log.warning("soniox preview failed: %s", type(e).__name__)
        raise PreviewError(f"could not reach the provider ({type(e).__name__})")

    if not audio:
        # terminated with no audio_end is how Soniox reports an abort. Saying
        # "no audio" beats returning an empty file the browser plays silently.
        raise PreviewError("the provider produced no audio")
    return bytes(audio)
