"""The hold message, rendered once and left on disk for Asterisk to play.

A caller who is waiting has not reached the agent. There is no LiveKit room, no
agent job, no speech recogniser and no language model - Asterisk is reading a
file. That is the entire point of the feature, so this file must exist BEFORE
the moment it is needed: synthesising on demand would put a provider request in
front of exactly the callers we are not spending money on, and it would do it
at the moment the system is already at its limit.

So it is made when somebody saves the message, and only then.

FORMAT - nothing is resampled and nothing is decoded. Asterisk reads raw signed
16-bit little-endian PCM from a file whose extension names the rate (.sln at
8 kHz, .sln16, .sln24), and every provider here can be asked for exactly that:

    soniox   audio_format=pcm_s16le with an explicit sample_rate -> 8 kHz raw
    sarvam   output_audio_codec=wav at 8 kHz -> a RIFF header we strip
    openai   response_format=pcm -> 24 kHz raw, no header, .sln24

8 kHz where the provider allows it, because that is what the call is: the trunk
carries 8 kHz and anything higher is transcoded down for nothing.

NAMING follows greeting_cache, for the same reason. The basename is a hash of
the text AND the provider, model and voice, so editing any of them asks for a
different file. There is nothing to clear and no way to leave yesterday's
message playing after somebody has changed it.

TWO PATHS, and confusing them would be a file that renders and never plays.
This process writes into a container directory; Asterisk is native and reads
the host one. The database stores the path ASTERISK will use.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import wave
from pathlib import Path

from . import ttspreview

log = logging.getLogger("admin-api")

# Where this process writes. A bind mount - see admin/docker-compose.yml.
WRITE_DIR = Path(os.getenv("HOLD_CACHE_DIR", "/data/hold"))

# What Asterisk sees at the other end of that mount, and therefore what goes in
# the database. Not derived from WRITE_DIR: they are two different filesystems'
# names for one directory, and guessing one from the other is how this breaks.
PLAY_DIR = os.getenv("HOLD_PLAY_DIR", "/opt/aivoice/cache/hold")

# What Asterisk's format_sln calls each rate. A rate that is not here cannot be
# played, so it is refused at save time rather than written and never heard.
_EXT = {8000: "sln", 16000: "sln16", 24000: "sln24"}

# Below this, the render did not finish. An unusable file matters more than it
# looks: it would be played confidently to every waiting caller from then on,
# and half a sentence on a loop is worse than silence because it sounds like
# the line is breaking up.
#
# 8000 samples * 2 bytes = one second at 8 kHz.
_MIN_BYTES = 16000

# What each provider is asked for. Soniox and Sarvam both do 8 kHz; OpenAI's
# pcm is fixed at 24 kHz and there is no parameter for it.
_RATE = {"soniox": 8000, "sarvam": 8000, "openai": 24000}


class RenderError(Exception):
    """The message could not be made. Carries the provider's words, never a key."""


def basename(text: str, provider: str, model: str | None,
             voice: str | None) -> str:
    """The file for this exact line in this exact voice."""
    return hashlib.sha256(
        "\x00".join((text, provider, model or "", voice or "")).encode()
    ).hexdigest()[:32]


def play_path(name: str) -> str:
    """What goes in the database: Asterisk's path, with NO extension.

    Playback() picks the extension itself - it looks for every format it can
    read and takes the best one. Handing it a full filename is how you get a
    "file does not exist" for a file that is plainly there.
    """
    return f"{PLAY_DIR}/{name}"


async def render(*, provider: str, api_key: str, model: str | None,
                 voice: str | None, language: str, text: str) -> str:
    """Synthesise, write, and return the path to store. Idempotent.

    Nothing is spent when the same words in the same voice are saved again: the
    name is a hash of exactly those things, so the file is already there.
    """
    if provider not in _RATE:
        raise RenderError(f"no hold-message support for provider '{provider}'")

    name = basename(text, provider, model, voice)
    target = WRITE_DIR / f"{name}.{_EXT[_RATE[provider]]}"
    if target.exists() and target.stat().st_size >= _MIN_BYTES:
        return play_path(name)

    pcm, rate = await _synthesise(provider, api_key, model, voice, language, text)

    if rate not in _EXT:
        raise RenderError(
            f"the provider returned {rate} Hz audio, which Asterisk cannot play")
    if len(pcm) < _MIN_BYTES:
        raise RenderError("the provider returned less than a second of audio")

    # The extension has to match what actually came back, not what was asked
    # for. A provider that quietly ignores a sample-rate parameter would
    # otherwise produce a file named 8 kHz and playing at 24, which is a
    # chipmunk on a loop and no error anywhere.
    target = WRITE_DIR / f"{name}.{_EXT[rate]}"

    WRITE_DIR.mkdir(parents=True, exist_ok=True)
    # Written beside the target and moved into place. Asterisk may be reading
    # this directory at any moment, and a half-written file is one it would
    # happily play.
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(pcm)
    tmp.replace(target)

    log.info("hold audio %s: %s, %d Hz, %.1fs", provider, target.name, rate,
             len(pcm) / (rate * 2))
    return play_path(name)


async def _synthesise(provider: str, api_key: str, model: str | None,
                      voice: str | None, language: str,
                      text: str) -> tuple[bytes, int]:
    """-> (raw signed 16-bit PCM, sample rate)."""
    want = _RATE[provider]
    try:
        if provider == "soniox":
            data = await ttspreview.soniox(
                api_key, model=model or "", voice=voice or "",
                language=language, text=text,
                audio_format="pcm_s16le", sample_rate=want)
        elif provider == "sarvam":
            data = await ttspreview.sarvam(
                api_key, model=model or "", voice=voice or "",
                language=language, text=text,
                codec="wav", sample_rate=want)
        else:
            data = await ttspreview.openai(
                api_key, model=model or "", voice=voice or "",
                language=language, text=text, response_format="pcm")
    except ttspreview.PreviewError as e:
        raise RenderError(str(e)) from e

    return _strip_header(data, want)


def _strip_header(data: bytes, assumed_rate: int) -> tuple[bytes, int]:
    """A RIFF file becomes its samples; anything else is already samples.

    Written this way rather than per provider because it is cheap and it cannot
    be wrong: the bytes say what they are. A provider that starts returning wav
    where it returned raw - or the reverse - is handled without anybody finding
    out on a call.
    """
    if not data.startswith(b"RIFF"):
        return data, assumed_rate

    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise RenderError(
                f"the provider returned {w.getsampwidth() * 8}-bit audio; "
                "Asterisk needs 16-bit")
        rate, channels = w.getframerate(), w.getnchannels()
        pcm = w.readframes(w.getnframes())

    if channels == 2:
        # Left channel only. Averaging would be the better mix, but a phone
        # line is mono and every provider here is asked for mono - this is the
        # path that should never run, kept so it degrades rather than plays
        # both channels interleaved at double speed.
        pcm = b"".join(pcm[i:i + 2] for i in range(0, len(pcm), 4))
        log.warning("hold audio came back in stereo; kept the left channel")
    elif channels != 1:
        raise RenderError(f"the provider returned {channels}-channel audio")

    return pcm, rate
