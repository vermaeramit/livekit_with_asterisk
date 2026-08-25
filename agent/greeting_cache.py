"""Play the greeting from disk instead of buying it from a provider again.

The greeting is the same sentence, in the same voice, on every single call. It
is also the one moment that cannot absorb a delay: the caller has just been
connected and has heard nothing yet. Today it costs 1458-1519 ms on a good run,
and on 25 August it cost 3946 ms and 6835 ms on two calls that were abandoned
before anybody heard a word.

Rendering it once and reading it back takes the provider out of that moment
entirely. A slow Soniox stops being the caller's problem.

The file name is a hash of the text AND the provider, model and voice. Change
any of them and the name changes with it, so the old audio is simply never asked
for again - there is nothing to remember to clear, and no way to leave a stale
greeting playing after somebody edits it in the console.

Nothing here is load-bearing. Every failure path leaves the caller with the
ordinary synthesised greeting, which is what they would have had anyway.
"""
from __future__ import annotations

import hashlib
import logging
import os
import wave
from collections.abc import AsyncIterator
from pathlib import Path

from livekit import rtc

logger = logging.getLogger("voice-agent")

CACHE_DIR = Path(os.getenv("GREETING_CACHE_DIR", "/opt/aivoice/cache/greetings"))

# A render this short did not finish. Refusing it matters more than it looks:
# an unusable cache entry would be played confidently on every call after this
# one, and a greeting that stops halfway is worse than a slow greeting.
MIN_BYTES = 8000

# 20 ms per frame - what the TTS plugins emit and what the room expects.
FRAME_MS = 20


def path_for(text: str, provider: str | None, model: str | None,
             voice: str | None) -> Path:
    """Where this exact greeting, in this exact voice, lives."""
    key = hashlib.sha256(
        "\x00".join((text, provider or "", model or "", voice or "")).encode()
    ).hexdigest()[:32]
    return CACHE_DIR / f"{key}.wav"


def frames(path: Path) -> AsyncIterator[rtc.AudioFrame] | None:
    """The cached audio ready to hand to say(), or None to synthesise instead.

    Read in full here rather than streamed off disk during playback: it is a few
    hundred kilobytes, and the point of this file is to not be waiting on
    anything once the caller is on the line.
    """
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                return None
            sample_rate, channels = w.getframerate(), w.getnchannels()
            pcm = w.readframes(w.getnframes())
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception("greeting cache unreadable, synthesising instead: %s", path)
        return None

    if len(pcm) < MIN_BYTES:
        return None

    async def gen() -> AsyncIterator[rtc.AudioFrame]:
        per_frame = (sample_rate * FRAME_MS // 1000) * channels * 2
        for i in range(0, len(pcm), per_frame):
            chunk = pcm[i:i + per_frame]
            yield rtc.AudioFrame(chunk, sample_rate, channels,
                                 len(chunk) // (2 * channels))

    return gen()


async def store(tts, text: str, path: Path) -> None:
    """Render the greeting once, for the calls after this one. Never raises.

    Deliberately not on the path of the call that finds the cache empty. That
    call speaks the ordinary way, so editing a greeting costs the next caller
    nothing worse than what every caller pays today.
    """
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.part")
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        pcm = bytearray()
        sample_rate = channels = None
        async for ev in tts.synthesize(text):
            frame = ev.frame
            pcm += bytes(frame.data)
            sample_rate, channels = frame.sample_rate, frame.num_channels

        if sample_rate is None or len(pcm) < MIN_BYTES:
            logger.warning("greeting render gave %d bytes - not cached", len(pcm))
            return

        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(bytes(pcm))

        # Six workers share this directory and any of them may be rendering the
        # same greeting right now. Rename is atomic, so the worst case is the
        # same audio written twice, never a half-written file being played.
        os.replace(tmp, path)
        os.chmod(path, 0o644)
        logger.info("greeting cached: %.1fs of audio -> %s",
                    len(pcm) / (sample_rate * channels * 2), path.name)
    except Exception:
        logger.exception("greeting render failed - calls will keep using the provider")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
