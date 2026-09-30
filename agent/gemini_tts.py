"""Gemini's own text-to-speech, spoken to directly.

NOT livekit-plugins-google. That plugin wraps Google CLOUD Text-to-Speech -
`texttospeech.googleapis.com`, service-account credentials, Chirp/Neural2
voices - which is a different product from the Gemini TTS models. It would not
reach these models at all, and importing it costs what `voice_agent` already
refused to pay once: the Google auth stack, loaded into every job process, for
a plugin most campaigns never use. See the note at voice_agent.py:38.

So this speaks to the Gemini API directly, in the shape kokoro_tts.py has:

    POST {base}/models/{model}:generateContent   (x-goog-api-key)
    {"contents":[{"parts":[{"text": ...}]}],
     "generationConfig":{"responseModalities":["AUDIO"],
                         "speechConfig":{"voiceConfig":{
                             "prebuiltVoiceConfig":{"voiceName": ...}}}}}
    <- {"candidates":[{"content":{"parts":[{"inlineData":{
           "mimeType":"audio/L16;codec=pcm;rate=24000","data":"<base64>"}}]}}]}

ONE RESPONSE, NOT A STREAM. `generateContent` returns the whole utterance in a
single JSON body with the samples base64-encoded inside it. Nothing arrives
early, so `streaming=False` here is the literal truth and livekit wraps this in
a StreamAdapter, exactly as it does Kokoro - which is what makes clause
splitting matter for this provider too.

THE RATE IS READ, NOT ASSUMED. `mimeType` carries it (`rate=24000`), and because
the whole body is in hand before a single frame is emitted, the emitter can be
initialised with what actually arrived rather than with what we hoped for. A
provider that quietly changed rate would otherwise play at the wrong speed with
no error anywhere - the same failure `holdaudio._strip_header` exists to avoid.

SPEED IS NOT SUPPORTED. There is no rate parameter on these models; delivery is
steered by prompting instead, and a style instruction mixed into the text is a
sentence the model may simply read out to the caller. So `tts_speed` is ignored
here and said so in the console - see tts_defaults.GEMINI_IGNORES_SPEED.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass, replace

import aiohttp
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
    utils,
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# What these models return today. Only a fallback: the rate that gets used is
# the one parsed out of the response's own mimeType.
SAMPLE_RATE = 24000
NUM_CHANNELS = 1

_RATE_IN_MIME = re.compile(r"rate=(\d+)")


@dataclass
class _Options:
    api_key: str
    model: str
    voice: str
    base_url: str


class TTS(tts.TTS):
    def __init__(self, *, api_key: str, model: str = "gemini-2.5-flash-preview-tts",
                 voice: str = "Kore", base_url: str = BASE_URL,
                 http_session: aiohttp.ClientSession | None = None) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        # The name this provider is recorded under everywhere: the call row's
        # tts_provider_used, the metrics label, the retry warnings - and the
        # key costing looks a rate up by. livekit's default would be
        # "gemini_tts.TTS", and a rate seeded under "gemini" would never match
        # it. Kokoro was priced as an unknown provider for six calls this way.
        self._label = "gemini"

        if not api_key:
            raise ValueError("gemini TTS needs an API key on the campaign or client")
        self._opts = _Options(api_key=api_key, model=model, voice=voice,
                              base_url=base_url.rstrip("/"))
        self._session = http_session

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "Gemini"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()
        return self._session

    def synthesize(self, text: str, *,
                   conn_options: APIConnectOptions | None = None) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text,
                             conn_options=conn_options or DEFAULT_API_CONNECT_OPTIONS)


def build_payload(text: str, voice: str) -> dict:
    """The request body. Shared with the console's preview so the two cannot drift."""
    return {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }


def extract_audio(body: dict) -> tuple[bytes, int]:
    """-> (raw signed 16-bit PCM, sample rate), or raise with the reason.

    Separate from the stream so the console's preview and the hold-message
    render read the response the same way this does. A 200 with no audio in it
    is the case that matters: the model can decline a line, and the emitter's
    "no audio frames were pushed" names the wrong culprit for that.
    """
    candidates = body.get("candidates") or []
    for cand in candidates:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if not inline or not inline.get("data"):
                continue
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            m = _RATE_IN_MIME.search(mime)
            return base64.b64decode(inline["data"]), int(m.group(1)) if m else SAMPLE_RATE

    # Nothing to play. Say why, in the provider's own words where it gave any.
    block = (body.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise APIStatusError(message=f"Gemini declined the text: {block}",
                             status_code=200, body=str(body)[:400])
    finish = candidates[0].get("finishReason") if candidates else None
    raise APIStatusError(
        message=f"Gemini returned no audio (finishReason={finish or 'none'})",
        status_code=200, body=str(body)[:400])


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: TTS, input_text: str,
                 conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts
        self._opts = replace(tts._opts)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        url = f"{self._opts.base_url}/models/{self._opts.model}:generateContent"
        try:
            async with self._tts._ensure_session().post(
                url=url,
                # In a header, never in the query string: a URL with a key in it
                # ends up in every proxy and access log between here and Google.
                headers={"x-goog-api-key": self._opts.api_key},
                json=build_payload(self._input_text, self._opts.voice),
                timeout=aiohttp.ClientTimeout(
                    total=self._conn_options.timeout,
                    sock_connect=self._conn_options.timeout,
                ),
            ) as res:
                # Read once. res.text() followed by res.json() happens to
                # work because aiohttp caches the body, but relying on that is
                # relying on an implementation detail to read a response twice.
                body = await res.text()
                if res.status != 200:
                    raise APIStatusError(
                        message=f"Gemini returned {res.status}: {body[:200]}",
                        status_code=res.status, body=body)
                data = json.loads(body)

            pcm, rate = extract_audio(data)

            # Initialised after the bytes are in hand, with the rate the
            # response declared rather than the one this module hoped for.
            output_emitter.initialize(
                request_id=utils.shortuuid(),
                sample_rate=rate,
                num_channels=NUM_CHANNELS,
                mime_type="audio/pcm",
            )
            output_emitter.push(pcm)
        except asyncio.TimeoutError as e:
            raise APITimeoutError("Gemini did not answer in time") from e
        except aiohttp.ClientError as e:
            raise APIConnectionError(f"could not reach Gemini: {e}") from e
