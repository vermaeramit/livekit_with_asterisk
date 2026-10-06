"""Raya's text-to-speech, spoken to directly.

NOT livekit-plugins-raya. One exists and works, but it is not on PyPI - it
installs from a git clone - and everything in agent/requirements.txt is pinned
to an exact version for a reason: this is the call path, and a dependency that
can change underneath a deploy is one nobody will notice changing. Speaking to
the endpoint ourselves is about ninety lines, which is less than the cost of
owning that risk.

    POST {base}/text-to-speech/stream        (X-API-Key)
    {"text", "voice_id", "language", "model", "codec": "pcm",
     "sample_rate", "speed"}
    <- text/event-stream
       data: {"type":"chunk","data":"<base64 PCM F32LE>","step_time":0.03}
       data: {"type":"done","done":true}

MEASURED 6 Oct 2026, from .243, one short Hindi line:

    24 kHz   ttfb 541 ms   first audio 571 ms   17 chunks
     8 kHz   ttfb 341 ms   first audio 342 ms   17 chunks

first audio and ttfb are the same number, which says the audio starts with the
response rather than after it - and step_time summed to 0.358 s for 2.56 s of
speech, seven times faster than real time. So the wait is reaching them, not
their work.

FLOAT32, which nothing else here returns. Every other provider gives signed
16-bit, which is what AudioEmitter slices into frames; a float stream read as
int16 is not an error, it is noise. The docs say F32LE and the probe confirmed
it - 500 of the first 500 samples inside [-1, 1] - so it is converted here, and
across chunk boundaries, because a 4-byte sample can be split between two SSE
frames and three quarters of one sample is enough to shift every sample after
it.

streaming=False, and that is the literal truth rather than a shortcoming:
livekit's flag means the TTS can accept a TOKEN stream and speak it as it
arrives, which this cannot - a whole sentence goes in one request. What streams
is the audio coming back. So the StreamAdapter wraps this and clause splitting
still matters, exactly as for kokoro and gemini.
"""
from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, replace

import aiohttp
import numpy as np
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
    utils,
)

BASE_URL = "https://hub.getraya.app/v1"

NUM_CHANNELS = 1

# What the service accepts. A rate it does not know is a 4xx on the first call
# of a campaign's life, which is a worse place to find out than here.
RATES = (8000, 16000, 22050, 24000)


def language_for(language: str) -> str:
    """-> the code Raya wants, from the regional one a campaign stores.

    Raya names most languages bare - hi, mr, te, kn, bn, as, gu, ne, ml, ta -
    and English by region: en-in and en-us. So the region is dropped except for
    English, where it is the whole point.
    """
    base, _, region = language.partition("-")
    base = base.lower()
    return f"{base}-{region.lower()}" if base == "en" and region else base


@dataclass
class _Options:
    api_key: str
    voice_id: str
    language: str
    model: str
    sample_rate: int
    speed: float
    base_url: str


class TTS(tts.TTS):
    def __init__(self, *, api_key: str, voice_id: str, language: str,
                 model: str = "m1", sample_rate: int = 24000,
                 speed: float = 1.0, base_url: str = BASE_URL,
                 http_session: aiohttp.ClientSession | None = None) -> None:
        if sample_rate not in RATES:
            raise ValueError(
                f"raya TTS does not serve {sample_rate} Hz - it offers "
                f"{', '.join(str(r) for r in RATES)}")
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        # The name this provider is recorded under everywhere: the call row's
        # tts_provider_used, the metrics label, the retry warnings - and the key
        # costing looks a rate up by. livekit's default would be
        # "raya_tts.TTS", and a rate seeded under "raya" would never match it.
        # Kokoro was priced as an unknown provider for six calls this way.
        self._label = "raya"

        if not api_key:
            raise ValueError("raya TTS needs an API key on the campaign or client")
        if not voice_id:
            raise ValueError(
                "raya TTS needs a voice - pick one on the Voice tab. The voice "
                "is an id, not a name, and there is no sensible default")
        self._opts = _Options(api_key=api_key, voice_id=voice_id,
                              language=language_for(language), model=model,
                              sample_rate=sample_rate, speed=speed,
                              base_url=base_url.rstrip("/"))
        self._session = http_session

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "Raya"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()
        return self._session

    def synthesize(self, text: str, *,
                   conn_options: APIConnectOptions | None = None) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text,
                             conn_options=conn_options or DEFAULT_API_CONNECT_OPTIONS)


def to_int16(f32: bytes) -> bytes:
    """F32LE samples in [-1, 1] -> signed 16-bit, which is what the emitter wants.

    Clipped rather than scaled to fit: a sample outside the range is a fault at
    the source, and quietly attenuating the whole utterance to accommodate it
    would hide that while making every call quieter.
    """
    f = np.frombuffer(f32, dtype="<f4")
    return (np.clip(f, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: TTS, input_text: str,
                 conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts
        self._opts = replace(tts._opts)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        payload = {
            "text": self._input_text,
            "voice_id": self._opts.voice_id,
            "language": self._opts.language,
            "model": self._opts.model,
            # Raw samples. mp3 and wav would mean decoding back to the samples
            # we already had, and mulaw would mean decoding too - the emitter
            # takes PCM.
            "codec": "pcm",
            "sample_rate": self._opts.sample_rate,
            "speed": self._opts.speed,
        }
        started = False
        # A float is four bytes and an SSE frame can end in the middle of one.
        carry = b""
        try:
            async with self._tts._ensure_session().post(
                url=f"{self._opts.base_url}/text-to-speech/stream",
                headers={"X-API-Key": self._opts.api_key,
                         "Accept": "text/event-stream"},
                json=payload,
                timeout=aiohttp.ClientTimeout(
                    total=self._conn_options.timeout,
                    sock_connect=self._conn_options.timeout,
                ),
            ) as res:
                if res.status != 200:
                    body = await res.text()
                    raise APIStatusError(
                        message=f"Raya returned {res.status}: {body[:200]}",
                        status_code=res.status, body=body)

                # Parsed out of a byte buffer rather than by iterating lines:
                # aiohttp caps a line at 64 KiB and raises past it, and an SSE
                # frame here carries base64 audio well beyond that.
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
                        data = obj.get("data")
                        if not data:
                            continue
                        carry += base64.b64decode(data)
                        whole = len(carry) - (len(carry) % 4)
                        if not whole:
                            continue
                        pcm, carry = to_int16(carry[:whole]), carry[whole:]
                        if not started:
                            output_emitter.initialize(
                                request_id=utils.shortuuid(),
                                sample_rate=self._tts.sample_rate,
                                num_channels=NUM_CHANNELS,
                                mime_type="audio/pcm",
                            )
                            started = True
                        output_emitter.push(pcm)

            if not started:
                # A 200 that carried no audio. Without this the emitter reports
                # "no audio frames were pushed", which accuses this code rather
                # than the service.
                raise APIStatusError(
                    message="Raya returned no audio for this text",
                    status_code=200, body="")
        except asyncio.TimeoutError as e:
            raise APITimeoutError("Raya did not answer in time") from e
        except aiohttp.ClientError as e:
            raise APIConnectionError(f"could not reach Raya: {e}") from e
