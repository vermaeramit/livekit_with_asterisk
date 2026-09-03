"""Synthesise a short line so somebody can hear a voice before choosing it.

Soniox offers 70 voices on tts-rt-v2. Picking one from a dropdown of names and
one-line descriptions is guessing, and the campaign only finds out what it
sounds like on a real call.

WHY THIS IS NOT THE AGENT'S CODE

The agent builds a `soniox.TTS` from the livekit plugin, which the admin image
does not have - those plugins live in the agent's venv. Adding them here would
pull livekit-agents and its dependency tree into this image for one preview
button.

So each provider is called directly, and the two are nothing alike:

  Soniox   a websocket, and a BARE language code ("hi")
  Sarvam   plain REST, and a REGIONAL one ("hi-IN")

The campaign stores the regional form because that is what Sarvam needs, so
Soniox is the one that gets converted. Sending it unchanged is a 400 - which is
exactly what happened the first time this ran.

`websockets` is already installed as part of uvicorn[standard] and `urllib`
covers the REST side, so neither provider needed a new dependency. Both wire
formats were read out of the installed plugins rather than remembered:

    ->  {"api_key", "model", "language", "voice", "audio_format",
         "sample_rate", "speed", "stream_id"}
    ->  {"stream_id", "text"}
    ->  {"stream_id", "text_end": true}
    <-  {"stream_id", "audio": "<base64>"}    repeated
    <-  {"stream_id", "audio_end": true}
    <-  {"stream_id", "terminated": true}
    <-  {"stream_id", "error_code", "error_message"}   on failure

Sarvam:

    POST https://api.sarvam.ai/text-to-speech
    api-subscription-key: <key>
    {"target_language_code", "text", "speaker", "pace", "model",
     "speech_sample_rate", "output_audio_codec", ...}
    ->  {"audios": ["<base64>"]}

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
import urllib.error
import urllib.request
import uuid

import websockets

log = logging.getLogger("admin-api")

SONIOX_WS = "wss://tts-rt.soniox.com/tts-websocket"
SARVAM_URL = "https://api.sarvam.ai/text-to-speech"

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
        # Soniox takes a bare ISO code. The campaign stores Sarvam's regional
        # form ("hi-IN") because that is what Sarvam needs, and sending it
        # unchanged is rejected with "Invalid language 'hi-IN'". The agent has
        # converted this since Soniox went in; the preview had not, because the
        # preview does not go through the agent's code at all.
        "language": language.split("-")[0].lower(),
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


def _sarvam_blocking(api_key: str, payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        SARVAM_URL, data=body, method="POST",
        headers={"api-subscription-key": api_key,
                 "Content-Type": "application/json",
                 # urllib's default User-Agent is a WAF magnet - the same note
                 # is on the tool caller in the agent.
                 "User-Agent": "AIVoice-Console/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # Sarvam puts the reason in the body, and it is worth passing on:
        # "insufficient quota" and "unknown speaker" need different answers.
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise PreviewError(f"{e.code}: {detail or e.reason}")
    except Exception as e:
        # Never the exception text: it can quote the request, and the request
        # carried the key in a header.
        log.warning("sarvam preview failed: %s", type(e).__name__)
        raise PreviewError(f"could not reach the provider ({type(e).__name__})")

    audios = data.get("audios") or []
    if not audios:
        raise PreviewError("the provider produced no audio")
    return b"".join(base64.b64decode(a) for a in audios)


async def sarvam(api_key: str, *, model: str, voice: str, language: str,
                 text: str, speed: float = 1.0) -> bytes:
    """-> mp3 bytes.

    The language goes through UNCHANGED. Sarvam wants the regional code and
    that is what the campaign stores, so the conversion Soniox needs would be
    a bug here.
    """
    payload = {
        "target_language_code": language,
        "text": text,
        "speaker": voice,
        "pace": speed,
        "model": model,
        "speech_sample_rate": 22050,
        "output_audio_codec": "mp3",
    }
    # Mirrors the plugin: these are rejected on the models that do not have
    # them, so they are sent only where the plugin sends them.
    if model == "bulbul:v2":
        payload["enable_preprocessing"] = True
    elif model in ("bulbul:v3", "bulbul:v3-beta"):
        payload["temperature"] = 0.6

    return await asyncio.to_thread(_sarvam_blocking, api_key, payload)
