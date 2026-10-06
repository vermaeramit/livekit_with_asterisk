"""Synthesise a short line so somebody can hear a voice before choosing it.

Soniox offers 70 voices on tts-rt-v2. Picking one from a dropdown of names and
one-line descriptions is guessing, and the campaign only finds out what it
sounds like on a real call.

WHY THIS IS NOT THE AGENT'S CODE

The agent builds a `soniox.TTS` from the livekit plugin, which the admin image
does not have - those plugins live in the agent's venv. Adding them here would
pull livekit-agents and its dependency tree into this image for one preview
button.

So each provider is called directly, and the three are nothing alike:

  Soniox   a websocket, and a BARE language code ("hi")
  Sarvam   plain REST, and a REGIONAL one ("hi-IN")
  OpenAI   the SDK, and NO language at all - the voice speaks whatever the
           text is written in

The campaign stores the regional form because that is what Sarvam needs, so
Soniox is the one that gets converted. Sending it unchanged is a 400 - which is
exactly what happened the first time this ran.

Nothing new was installed for any of them: `websockets` comes with
uvicorn[standard], `urllib` covers the REST side, and the `openai` SDK is
already here for embeddings. Every wire format below was read out of the
installed code rather than remembered:

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

OpenAI: the SDK's own audio.speech, called the way the livekit plugin calls it -
with_streaming_response and iter_bytes, rather than reaching for .content and
finding out at runtime which of the two an async client hands back.

That duplication is a real cost: if Soniox changes the protocol, the plugin gets
updated and this does not. It is bounded - a preview breaking is not a call
breaking - and it is the reason this file says exactly where the shapes came
from.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave

import websockets

# The Soniox host depends on the region its key was issued in, and that mapping
# lives with the keys - see provider_keys.soniox_host and migration 058.
from . import googleauth
from .provider_keys import soniox_host

log = logging.getLogger("admin-api")

SARVAM_URL = "https://api.sarvam.ai/text-to-speech"

# Gemini's own TTS models - NOT texttospeech.googleapis.com, which is Google
# Cloud TTS: a different product, different voices, and a service-account
# credential that would not fit provider_keys. See agent/gemini_tts.py.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"

# Google Cloud Text-to-Speech. A different product from GEMINI_URL above and a
# different credential - a service account, exchanged for a bearer token by
# googleauth, because this image has no livekit plugin to do it for us.
GOOGLE_URL = "https://texttospeech.googleapis.com/v1"

# Raya (Bakbak). A plain API key in X-API-Key, and the one-shot endpoint rather
# than the streaming one: a preview plays a whole clip, so there is nothing for
# SSE to save here and a complete body is simpler to be wrong about.
RAYA_URL = "https://hub.getraya.app/v1"
_GEMINI_RATE_IN_MIME = re.compile(r"rate=(\d+)")

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
                 text: str, speed: float = 1.0,
                 audio_format: str = _FORMAT,
                 sample_rate: int | None = None,
                 region: str | None = None) -> bytes:
    """-> mp3 bytes, or raw PCM when asked for it.

    audio_format/sample_rate exist for the hold-message render, which needs
    signed 16-bit PCM at a telephony rate rather than something a browser can
    play. Defaults leave the preview exactly as it was.
    """
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
        "audio_format": audio_format,
        "speed": speed,
        "stream_id": stream_id,
    }
    # "Required for raw audio formats" - the plugin's own words. Sending it for
    # mp3 as well would be harmless, but sending it ONLY where it is needed is
    # what keeps the preview request byte-identical to what it was.
    if sample_rate is not None:
        config["sample_rate"] = sample_rate

    audio = bytearray()
    try:
        url = f"wss://{soniox_host('tts-rt', region)}/tts-websocket"
        async with websockets.connect(url, max_size=None) as ws:
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
                 text: str, speed: float = 1.0,
                 codec: str = _FORMAT, sample_rate: int = 22050) -> bytes:
    """-> mp3 bytes, or a wav when asked for one.

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
        "speech_sample_rate": sample_rate,
        "output_audio_codec": codec,
    }
    # Mirrors the plugin: these are rejected on the models that do not have
    # them, so they are sent only where the plugin sends them.
    if model == "bulbul:v2":
        payload["enable_preprocessing"] = True
    elif model in ("bulbul:v3", "bulbul:v3-beta"):
        payload["temperature"] = 0.6

    return await asyncio.to_thread(_sarvam_blocking, api_key, payload)


async def openai(api_key: str, *, model: str, voice: str, language: str,
                 text: str, speed: float = 1.0,
                 response_format: str = _FORMAT,
                 base_url: str | None = None) -> bytes:
    """-> mp3 bytes, or raw PCM when asked for it.

    OpenAI's "pcm" is documented as 24 kHz, 16-bit, mono, little-endian, with
    no header - which is exactly what Asterisk reads from a .sln24 file.

    `language` is accepted and ignored, so the three providers share one
    signature. OpenAI has no language parameter: the voice speaks whatever the
    text is written in, which is why a Hindi campaign on OpenAI must have Hindi
    in the box rather than a language setting somewhere.

    Streamed and accumulated, matching the livekit plugin exactly. The
    non-streaming call returns an object whose bytes are reached differently
    depending on the client, and that is not a thing to discover in production.
    """
    from openai import AsyncOpenAI

    # base_url points this at something other than OpenAI. Kokoro on our own
    # GPU box serves the same wire format, so the preview a campaign owner
    # hears comes from the same code path a call uses - see kokoro() below.
    client = AsyncOpenAI(api_key=api_key,
                         **({"base_url": base_url} if base_url else {}))
    audio = bytearray()
    try:
        async with client.audio.speech.with_streaming_response.create(
            input=text, model=model, voice=voice,
            response_format=response_format, speed=speed,
        ) as stream:
            async for chunk in stream.iter_bytes():
                audio.extend(chunk)
    except Exception as e:
        # OpenAI's own message where there is one - "voice not found" and "quota
        # exceeded" need different answers - and never the raw exception, which
        # can quote a request that carried the key.
        detail = getattr(e, "message", None) or type(e).__name__
        log.warning("openai preview failed: %s", type(e).__name__)
        raise PreviewError(str(detail)[:200])
    finally:
        await client.close()

    if not audio:
        raise PreviewError("the provider produced no audio")
    return bytes(audio)


def kokoro_url() -> str:
    """-> where our own text-to-speech answers, or "" if this server has not
    been told.

    No default, for the reason migration 059 gives: a LAN address differs
    between servers and a hardcoded one already sent production's calls to the
    development box for two days. The agent reads the same variable.
    """
    return os.getenv("KOKORO_URL", "").strip()


async def kokoro(api_key: str, *, model: str, voice: str, language: str,
                 text: str, speed: float = 1.0,
                 response_format: str = _FORMAT,
                 sample_rate: int | None = None) -> bytes:
    """Our own TTS, through the OpenAI path it already speaks.

    `api_key` is accepted and ignored so this has the same signature as the
    other three and the dispatch above needs no special case. There is nobody
    to authenticate to: the box is on the LAN, reachable only from 10.130.0.0/16
    by its own firewall.

    sample_rate is accepted and ignored too - Kokoro serves 24 kHz and offers no
    way to ask for another. The hold-message render passes one to every provider
    and checks what came back, which catches this rather than trusting it.
    """
    url = kokoro_url()
    if not url:
        raise PreviewError(
            "KOKORO_URL is not set on this server, so our own voice cannot be "
            "previewed here")
    return await openai(api_key or "not-needed", model=model, voice=voice,
                        language=language, text=text, speed=speed,
                        response_format=response_format, base_url=url)


def _wav(pcm: bytes, rate: int) -> bytes:
    """Raw samples wrapped so a browser will play them.

    Only Gemini needs this. The other providers can return mp3 and do; these
    models return raw PCM and nothing else, so the header has to come from
    somewhere and this is the cheapest somewhere - no encoder, no dependency.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _gemini_blocking(api_key: str, model: str, payload: dict) -> tuple[bytes, int]:
    """-> (raw signed 16-bit PCM, sample rate as the response declared it)."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{GEMINI_URL}/models/{model}:generateContent", data=body, method="POST",
        # In a header, not the query string: a URL carrying a key is written to
        # every proxy and access log between here and Google.
        headers={"x-goog-api-key": api_key,
                 "Content-Type": "application/json",
                 "User-Agent": "AIVoice-Console/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise PreviewError(f"{e.code}: {detail or e.reason}")
    except Exception as e:
        # Never the exception text: it can quote the request, and the request
        # carried the key in a header.
        log.warning("gemini preview failed: %s", type(e).__name__)
        raise PreviewError(f"could not reach the provider ({type(e).__name__})")

    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if not inline or not inline.get("data"):
                continue
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            m = _GEMINI_RATE_IN_MIME.search(mime)
            return base64.b64decode(inline["data"]), int(m.group(1)) if m else 24000

    # A 200 with no audio in it. The model can decline a line, and "produced no
    # audio" on its own sends somebody looking at the network.
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise PreviewError(f"Gemini declined the text: {block}")
    raise PreviewError("the provider produced no audio")


async def gemini(api_key: str, *, model: str, voice: str, language: str,
                 text: str, speed: float = 1.0,
                 response_format: str = _FORMAT) -> bytes:
    """-> a WAV the browser can play, or raw PCM when asked for it.

    `language` is accepted and ignored, like OpenAI's: these models have no
    language parameter and speak whatever the text is written in. A Hindi
    campaign on Gemini therefore needs Hindi in the box, not a setting.

    `speed` is accepted and ignored too, and that one is worth stating rather
    than hiding: there is no rate parameter on these models. Delivery is steered
    by prompting, and an instruction glued onto the caller's sentence is a line
    the model may read out loud. The console says so beside the slider.
    """
    pcm, rate = await asyncio.to_thread(
        _gemini_blocking, api_key, model,
        {"contents": [{"parts": [{"text": text}]}],
         "generationConfig": {
             "responseModalities": ["AUDIO"],
             "speechConfig": {
                 "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}})
    return pcm if response_format == "pcm" else _wav(pcm, rate)


def _google_blocking(token: str, path: str, payload: dict | None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{GOOGLE_URL}{path}", data=body,
        method="POST" if payload is not None else "GET",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "User-Agent": "AIVoice-Console/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise PreviewError(f"{e.code}: {detail or e.reason}")
    except Exception as e:
        # Never the exception text: it can quote the request, and the request
        # carried a bearer token.
        log.warning("google tts failed: %s", type(e).__name__)
        raise PreviewError(f"could not reach the provider ({type(e).__name__})")


async def google(api_key: str, *, model: str, voice: str, language: str,
                 text: str, speed: float = 1.0,
                 response_format: str = _FORMAT,
                 sample_rate: int | None = None,
                 pitch: float = 0.0) -> bytes:
    """-> mp3 bytes, or LINEAR16 when asked for pcm.

    `api_key` is the service-account JSON, as stored - one encrypted string,
    parsed here. The name is kept because every provider in this module shares
    one signature and the dispatch above has no special cases.

    `model` is ignored. Cloud TTS carries the model family inside the voice
    name - hi-IN-Chirp3-HD-... IS the model - so there is nothing else to set.
    See tts_defaults.GOOGLE_IGNORES_MODEL.

    LINEAR16 comes back inside a RIFF container rather than raw. That needs no
    handling here: holdaudio._strip_header reads what arrived instead of
    trusting what was asked for, which is exactly the case it was written for.
    """
    try:
        creds = googleauth.parse(api_key)
        token = await googleauth.access_token(creds)
    except googleauth.AuthError as e:
        raise PreviewError(str(e))

    audio_cfg: dict = {
        "audioEncoding": "LINEAR16" if response_format == "pcm" else "MP3",
        "speakingRate": speed,
        "pitch": pitch,
    }
    if sample_rate:
        audio_cfg["sampleRateHertz"] = sample_rate

    voice_cfg: dict = {"languageCode": language}
    # Left out when empty so Google picks for the language. A name we invented
    # is a 400 at best and the wrong voice at worst.
    if voice:
        voice_cfg["name"] = voice

    data = await asyncio.to_thread(
        _google_blocking, token, "/text:synthesize",
        {"input": {"text": text}, "voice": voice_cfg, "audioConfig": audio_cfg})

    content = data.get("audioContent")
    if not content:
        raise PreviewError("the provider produced no audio")
    return base64.b64decode(content)


async def google_voices(api_key: str, language: str) -> list[dict]:
    """-> what Google actually serves for this language, right now.

    Read rather than listed for the reason the Soniox catalogue is read from
    Soniox: Google publishes hundreds of voices across four model families and
    a literal here would go stale without anybody noticing until a call failed.
    """
    try:
        creds = googleauth.parse(api_key)
        token = await googleauth.access_token(creds)
    except googleauth.AuthError as e:
        raise PreviewError(str(e))

    q = urllib.parse.quote(language)
    data = await asyncio.to_thread(_google_blocking, token,
                                   f"/voices?languageCode={q}", None)
    return data.get("voices") or []


def _raya_blocking(api_key: str, path: str, payload: dict | None) -> dict | bytes:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{RAYA_URL}{path}", data=body,
        method="POST" if payload is not None else "GET",
        headers={"X-API-Key": api_key, "Content-Type": "application/json",
                 "User-Agent": "AIVoice-Console/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise PreviewError(f"{e.code}: {detail or e.reason}")
    except Exception as e:
        # Never the exception text: it can quote the request, and the request
        # carried the key in a header.
        log.warning("raya request failed: %s", type(e).__name__)
        raise PreviewError(f"could not reach the provider ({type(e).__name__})")

    if "json" in ctype:
        return json.loads(raw.decode("utf-8", "replace"))
    return raw


async def raya(api_key: str, *, model: str, voice: str, language: str,
               text: str, speed: float = 1.0,
               response_format: str = _FORMAT,
               sample_rate: int = 24000) -> bytes:
    """-> mp3 bytes, or raw signed 16-bit PCM when asked for it.

    Raya's own `pcm` is FLOAT32, which nothing else here returns and which no
    browser and no Asterisk will play. So pcm is asked for as `wav`, which
    arrives as ordinary 16-bit inside a RIFF container - and holdaudio._strip_
    header already reads what arrived rather than trusting what was requested,
    which is exactly the case it exists for.

    `language` is converted, not passed through: a campaign stores hi-IN and
    Raya wants hi. See agent/raya_tts.language_for - the rule is shared in
    spirit, kept separate because this image cannot import the agent.
    """
    base, _, region = (language or "").partition("-")
    base = base.lower()
    lang = f"{base}-{region.lower()}" if base == "en" and region else base

    data = await asyncio.to_thread(
        _raya_blocking, api_key, "/text-to-speech",
        {"text": text, "voice_id": voice, "language": lang,
         "model": model or "m1",
         "codec": "wav" if response_format == "pcm" else "mp3",
         "sample_rate": sample_rate, "speed": speed})
    if isinstance(data, dict):
        # Some shapes return the audio base64 in a field rather than as bytes.
        for field in ("audio", "data", "audio_content"):
            if data.get(field):
                return base64.b64decode(data[field])
        raise PreviewError("the provider produced no audio")
    return data


async def raya_voices(api_key: str) -> list[dict]:
    """-> every voice Raya serves, with its language and model.

    Read rather than listed for the reason the Soniox catalogue is: a literal
    goes stale and nobody finds out until a call fails. Raya's voices are UUIDs,
    which makes a stale local copy worse than useless - there is no name to
    recognise.
    """
    data = await asyncio.to_thread(_raya_blocking, api_key, "/voices", None)
    if not isinstance(data, dict):
        raise PreviewError("the voice list did not come back as JSON")
    return data.get("voices") or []
