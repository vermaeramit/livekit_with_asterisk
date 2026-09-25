"""Our own speech recognition, on the GPU box.

Qwen3-ASR served by vLLM, which exposes OpenAI's `/v1/audio/transcriptions`.
Written directly rather than through livekit's OpenAI STT plugin for the reason
kokoro_tts.py exists: that plugin received Kokoro's audio and handed the emitter
nothing, and a layer that has already failed once is not one to build a second
provider on.

NOT STREAMING, and that is a real difference from Soniox rather than a detail.
Soniox returns interim transcripts while the caller is still speaking, and the
turn detector reads them. This returns one transcript per utterance, after the
VAD has decided the caller stopped - livekit wraps a non-streaming STT in a
StreamAdapter with the session's VAD and does that chunking itself
(agents/voice/agent.py:485). Whether that helps or hurts the 1500 ms the turn
detector currently spends is the thing only a real call can answer.

Measured from .243 on 25 Sep 2026, against synthetic 8 kHz Hindi:

    0.5 s of audio   195 ms        a fixed cost of about 185 ms,
    1 s              213 ms        plus roughly 20 ms per second of audio
    2 s              228 ms
    12 s             880-970 ms

So the tail chunk a call actually sends costs around 200-230 ms.

There is no API key. The box answers 10.130.0.0/16 only, by its own firewall.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

import aiohttp
from livekit import rtc
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    stt,
    utils,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given


@dataclass
class _Options:
    base_url: str
    model: str
    language: str
    prompt: str | None


def bare_language(language: str) -> str:
    """"hi-IN" -> "hi". The campaign stores Sarvam's regional form because that
    is what Sarvam needs; every other provider here wants the bare code."""
    return (language or "hi").split("-")[0].lower()


class STT(stt.STT):
    def __init__(self, *, base_url: str, model: str = "qwen3-asr",
                 language: str = "hi", prompt: str | None = None,
                 http_session: aiohttp.ClientSession | None = None) -> None:
        """`base_url` includes the /v1, e.g. http://10.130.9.248:8001/v1.

        `prompt` is the campaign's vocabulary - its product names. Qwen3-ASR is
        trained to take background text and bias towards it, which is the one
        feature that made it the first candidate: the words this system gets
        wrong are names, and "Splendor Plus Flex" arriving as "Lender Plus
        Flex" once matched a different bike and ended a call.

        NOT YET VERIFIED that vLLM forwards OpenAI's `prompt` field to the
        model as recognition context. It is sent because the field is part of
        the OpenAI transcription API and costs nothing if ignored; whether it
        does anything is a measurement nobody has taken here.
        """
        super().__init__(capabilities=stt.STTCapabilities(
            streaming=False, interim_results=False))
        if not base_url:
            raise ValueError("qwen STT needs a base_url - see QWEN_STT_URL")
        self._label = "qwen"
        self._opts = _Options(base_url=base_url.rstrip("/"), model=model,
                              language=bare_language(language), prompt=prompt)
        self._session = http_session

    @property
    def provider(self) -> str:
        return "Qwen3-ASR"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()
        return self._session

    async def _recognize_impl(self, buffer: AudioBuffer, *,
                              language: NotGivenOr[str] = NOT_GIVEN,
                              conn_options: APIConnectOptions) -> stt.SpeechEvent:
        opts = replace(self._opts)
        if is_given(language):
            opts.language = bare_language(language)

        wav = rtc.combine_audio_frames(buffer).to_wav_bytes()
        form = aiohttp.FormData()
        form.add_field("file", wav, filename="audio.wav",
                       content_type="audio/wav")
        form.add_field("model", opts.model)
        form.add_field("language", opts.language)
        if opts.prompt:
            form.add_field("prompt", opts.prompt)

        try:
            async with self._ensure_session().post(
                url=f"{opts.base_url}/audio/transcriptions",
                data=form,
                timeout=aiohttp.ClientTimeout(
                    total=conn_options.timeout,
                    sock_connect=conn_options.timeout,
                ),
            ) as res:
                if res.status != 200:
                    body = await res.text()
                    raise APIStatusError(
                        message=f"Qwen3-ASR returned {res.status}: {body[:200]}",
                        status_code=res.status, body=body)
                data = await res.json()
        except asyncio.TimeoutError as e:
            raise APITimeoutError("Qwen3-ASR did not answer in time") from e
        except aiohttp.ClientError as e:
            # The address on purpose: the likeliest cause is QWEN_STT_URL
            # pointing at a box that is not running this.
            raise APIConnectionError(
                f"could not reach Qwen3-ASR at {opts.base_url}: {e}") from e

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(
                text=(data.get("text") or "").strip(),
                language=opts.language,
            )],
        )
