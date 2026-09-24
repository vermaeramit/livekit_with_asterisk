"""Our own text-to-speech, spoken to directly.

Kokoro runs on the GPU box behind an OpenAI-compatible API, so the obvious
integration was livekit's OpenAI TTS plugin with a `base_url`. That was tried
first and it does not work, and the reason is worth writing down because the
symptom accuses the wrong thing:

    tts-bench        "no audio frames were pushed", three retries, APIError
    curl             200, 87,916 bytes of audio/pcm
    the SDK alone    200, 108,566 bytes in 3 chunks
    Kokoro's log     every attempt, including the plugin's, 200 OK
    the emitter      initialize x4, flush x4, push x0 - ZERO bytes

Everything arrives and the plugin hands the emitter nothing. The fault is
somewhere between the OpenAI SDK's streaming response and that plugin, and it
is not ours to fix. Nor is it worth a workaround: the plugin exists to talk to
OpenAI, and what we need from Kokoro is one POST and its bytes.

So this speaks to it directly. It is the Sarvam plugin's shape - the one that
has carried every Sarvam call in this system - with the Sarvam parts removed:

    POST {base_url}/audio/speech
    {"model", "voice", "input", "response_format": "pcm", "speed"}
    <- raw signed 16-bit little-endian PCM at 24 kHz, streamed

`pcm`, not mp3, and not because mp3 failed. This server is on our own LAN and
serves exactly the rate the emitter assembles frames at, so encoding to mp3 and
decoding it back is CPU on two machines to arrive at the samples we already had.

There is no API key. The box is reachable only from 10.130.0.0/16 by its own
firewall, and there is no account to authenticate to - see migration 059.
"""
from __future__ import annotations

import asyncio
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

# Kokoro's own output rate. Not negotiable through the API and not worth
# resampling here: 24 kHz is what the emitter assembles frames at anyway.
SAMPLE_RATE = 24000
NUM_CHANNELS = 1


@dataclass
class _Options:
    base_url: str
    model: str
    voice: str
    speed: float


class TTS(tts.TTS):
    def __init__(self, *, base_url: str, model: str = "kokoro",
                 voice: str = "hf_alpha", speed: float = 1.0,
                 http_session: aiohttp.ClientSession | None = None) -> None:
        """`base_url` includes the /v1, e.g. http://10.130.9.248:8880/v1."""
        super().__init__(
            # One POST per sentence, no persistent connection. livekit wraps a
            # non-streaming TTS in a StreamAdapter, which splits the text into
            # sentences and synthesises them one at a time - and at ~200 ms per
            # sentence there is nothing a websocket would save.
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        # What this provider is CALLED, everywhere a name is recorded: the call
        # row's tts_provider_used, the metrics label, the retry warnings.
        #
        # livekit's default is "<module>.<class>", which gave "kokoro_tts.TTS".
        # The agent turns a label into a provider name by taking the third part
        # of "livekit.plugins.<name>.tts.TTS" and keeping anything else as-is,
        # so six calls were recorded as "kokoro_tts.TTS" - and costing looks a
        # rate up BY THAT NAME. The seeded rate is under "kokoro", so those
        # calls priced as though the provider were unknown.
        self._label = "kokoro"

        if not base_url:
            raise ValueError("kokoro TTS needs a base_url - see KOKORO_URL")
        self._opts = _Options(base_url=base_url.rstrip("/"), model=model,
                              voice=voice, speed=speed)
        self._session = http_session

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "Kokoro"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()
        return self._session

    def synthesize(self, text: str, *,
                   conn_options: APIConnectOptions | None = None) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text,
                             conn_options=conn_options or DEFAULT_API_CONNECT_OPTIONS)


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: TTS, input_text: str,
                 conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts
        self._opts = replace(tts._opts)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        payload = {
            "model": self._opts.model,
            "voice": self._opts.voice,
            "input": self._input_text,
            # Raw samples. The emitter recognises audio/pcm as raw and slices
            # it into frames itself - no decoder, no container, no header.
            "response_format": "pcm",
            "speed": self._opts.speed,
        }
        try:
            async with self._tts._ensure_session().post(
                url=f"{self._opts.base_url}/audio/speech",
                json=payload,
                timeout=aiohttp.ClientTimeout(
                    total=self._conn_options.timeout,
                    sock_connect=self._conn_options.timeout,
                ),
            ) as res:
                if res.status != 200:
                    body = await res.text()
                    raise APIStatusError(
                        message=f"Kokoro returned {res.status}: {body[:200]}",
                        status_code=res.status, body=body)

                # After the status check, so a failure is reported as itself
                # rather than as "no audio frames were pushed".
                output_emitter.initialize(
                    # Kokoro sends no request id and needs none; the emitter
                    # only logs a warning for an empty one, and a warning per
                    # sentence is noise in a worker log.
                    request_id=utils.shortuuid(),
                    sample_rate=self._tts.sample_rate,
                    num_channels=self._tts.num_channels,
                    mime_type="audio/pcm",
                )
                async for chunk in res.content.iter_chunked(4096):
                    output_emitter.push(chunk)
        except asyncio.TimeoutError as e:
            raise APITimeoutError("Kokoro did not answer in time") from e
        except aiohttp.ClientError as e:
            # The address is in the message on purpose: the likeliest cause is
            # KOKORO_URL pointing at a box that is not running this.
            raise APIConnectionError(
                f"could not reach Kokoro at {self._opts.base_url}: {e}") from e
