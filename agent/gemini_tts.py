"""Gemini's own text-to-speech, spoken to directly.

NOT livekit-plugins-google. That plugin wraps Google CLOUD Text-to-Speech -
`texttospeech.googleapis.com`, service-account credentials, Chirp/Neural2
voices - which is a different product from the Gemini TTS models. It would not
reach these models at all, and importing it costs what `voice_agent` already
refused to pay once: the Google auth stack, loaded into every job process, for
a plugin most campaigns never use. See the note at voice_agent.py:38.

So this speaks to the Gemini API directly, in the shape kokoro_tts.py has:

    POST {base}/models/{model}:streamGenerateContent?alt=sse   (x-goog-api-key)
    {"contents":[{"parts":[{"text": ...}]}],
     "generationConfig":{"responseModalities":["AUDIO"],
                         "speechConfig":{"voiceConfig":{
                             "prebuiltVoiceConfig":{"voiceName": ...}}}}}
    <- {"candidates":[{"content":{"parts":[{"inlineData":{
           "mimeType":"audio/L16;codec=pcm;rate=24000","data":"<base64>"}}]}}]}

STREAMING, AND THE MEASUREMENT THAT DECIDED IT. The first version called
`generateContent`, which answers with one complete JSON body: nothing arrives
until the whole clip is generated, base64'd and sent. Measured on .243, 30 Sep
2026, and the cost was a flat one - 17 characters took 2524 ms and 95 took
3097, or about 2400 ms fixed plus 7.3 ms per character. A 1.6 s clip cost what
an 8.7 s one did, so the wait was never the speech being made.

`streamGenerateContent?alt=sse` emits the audio in pieces as it is produced.
Against the same key, same model, same lines:

    first audio        short        long
    generateContent    2256/2857    3640/4089 ms
    streaming          1649/1565    2013/1967 ms

On a real-length line that is 1.6-2.1 seconds back. And time-to-first-byte and
time-to-first-AUDIO are the same number on the streaming call (1967 -> 1967),
which says the first chunk carries samples rather than a preamble.

TCP+TLS to the host measured 43 ms at best, so none of this is distance - the
question llm-net.py asks of the language model, asked here and answered.

Still `streaming=False` to livekit, and that is not a contradiction: livekit's
flag means "can accept a token stream and speak it as it arrives", which this
cannot - a whole sentence still goes in one request. What streams is the audio
coming back. So the StreamAdapter still wraps this, and clause splitting still
matters.

THE RATE IS READ, NOT ASSUMED. `mimeType` carries it (`rate=24000`), and the
emitter is initialised from the FIRST chunk that carries audio rather than from
a constant. A provider that quietly changed rate would otherwise play at the
wrong speed with no error anywhere - the failure `holdaudio._strip_header`
exists to avoid.

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


def audio_of(body: dict) -> tuple[bytes, int] | None:
    """-> (raw signed 16-bit PCM, sample rate) from one response object, or None.

    Does not raise. On the streaming call most chunks carry audio and the last
    one carries only a finishReason, so "no audio in this chunk" is ordinary
    rather than a failure - it is only a failure if NO chunk had any.
    """
    for cand in body.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if not inline or not inline.get("data"):
                continue
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            m = _RATE_IN_MIME.search(mime)
            return base64.b64decode(inline["data"]), int(m.group(1)) if m else SAMPLE_RATE
    return None


def why_no_audio(body: dict) -> str:
    """The provider's own reason, for a response that carried none.

    The model can decline a line, and the emitter's "no audio frames were
    pushed" names the wrong culprit for that - it accuses this code.
    """
    block = (body.get("promptFeedback") or {}).get("blockReason")
    if block:
        return f"Gemini declined the text: {block}"
    cands = body.get("candidates") or []
    finish = cands[0].get("finishReason") if cands else None
    return f"Gemini returned no audio (finishReason={finish or 'none'})"


def extract_audio(body: dict) -> tuple[bytes, int]:
    """-> (raw PCM, rate), or raise with the reason. The non-streaming shape.

    Kept for the console's preview and the hold-message render, which call
    generateContent: neither is on a phone call, so neither pays for streaming.
    """
    got = audio_of(body)
    if got:
        return got
    raise APIStatusError(message=why_no_audio(body), status_code=200,
                         body=str(body)[:400])


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: TTS, input_text: str,
                 conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts
        self._opts = replace(tts._opts)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        url = (f"{self._opts.base_url}/models/{self._opts.model}"
               ":streamGenerateContent?alt=sse")
        started = False
        last: dict = {}
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
                if res.status != 200:
                    body = await res.text()
                    raise APIStatusError(
                        message=f"Gemini returned {res.status}: {body[:200]}",
                        status_code=res.status, body=body)

                # Parsed out of a byte buffer rather than by iterating lines.
                # aiohttp's line reader caps a line at 64 KiB and raises when
                # one is longer; an SSE frame here carries base64 audio and goes
                # well past that. Reading raw and splitting on newlines has no
                # such ceiling.
                buf = b""
                async for chunk in res.content.iter_chunked(16384):
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.strip()
                        if not line.startswith(b"data:"):
                            continue
                        try:
                            obj = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        last = obj
                        got = audio_of(obj)
                        if not got:
                            continue
                        pcm, rate = got
                        if not started:
                            # Initialised from the first chunk that actually
                            # carries samples, with the rate it declared.
                            output_emitter.initialize(
                                request_id=utils.shortuuid(),
                                sample_rate=rate,
                                num_channels=NUM_CHANNELS,
                                mime_type="audio/pcm",
                            )
                            started = True
                        output_emitter.push(pcm)

            if not started:
                # A 200 that said nothing. Without this the emitter reports "no
                # audio frames were pushed", which accuses this code rather than
                # naming the model's own reason.
                raise APIStatusError(message=why_no_audio(last),
                                     status_code=200, body=str(last)[:400])
        except asyncio.TimeoutError as e:
            raise APITimeoutError("Gemini did not answer in time") from e
        except aiohttp.ClientError as e:
            raise APIConnectionError(f"could not reach Gemini: {e}") from e
