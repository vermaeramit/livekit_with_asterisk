"""Break text where a speaker would pause, not only where a sentence ends.

WHY THIS EXISTS

Our TTS is not streaming: livekit wraps it in a StreamAdapter which splits the
model's text and synthesises one piece at a time. The default splitter breaks on
`.` `!` `?` - so a reply written as ONE long clause-joined sentence gives it
nothing to split on until the model has finished writing the whole thing.

Measured on call 670, campaign 7, with Qwen3-32B on the GPU box:

    first_audio=3945ms   text_complete=-241ms   audio=10.0s
    first_audio=2325ms   text_complete=-235ms   audio=4.1s

A NEGATIVE text_complete means the entire answer was written before any audio
started. At ~23 tokens/s that is 3.7 seconds of the caller hearing nothing,
while the model's FIRST token had arrived in 200 ms.

Breaking on clause boundaries starts the voice after "ठीक है अमित जी," instead
of after the whole reply. The chunks are still whole clauses, so the text a
speaker sees is unchanged - only where it is cut.

WHAT IT MUST NOT BREAK

This system says prices and times out loud. A comma inside ₹52,540 and a colon
inside 10:30 are not pauses, and cutting there would have the voice say "fifty
two" and then "five hundred and forty" as separate breaths. So those two marks
break only when what follows them is a space, and what precedes them is not a
digit. Full stops and the danda need no such guard.

MIN LENGTH is what keeps this from becoming a stutter, and it is a trade: the
FIRST chunk is the one that decides when the caller hears anything, and holding
it back to make it tidier is the whole problem repeated smaller.

The arithmetic says small is safe. Speech runs at 12.7 characters per second on
this system; the model writes at about 23 tokens/s, which in Devanagari is
roughly 57 characters per second - four times faster than the voice can say
them. So once the first chunk is out, later ones are always ready before they
are needed, whatever their size.

14 leaves "ठीक है अमित जी," to go out on its own - which is FIFTEEN code
points, not the twenty it looks like: in Devanagari the matras are separate
characters, so a threshold set by eye holds back more than intended. That is
about 1.2 s of audio, while the next chunk needs around 1 s to write and
synthesise. What
this cannot settle is how it SOUNDS: each chunk is a separate synthesis, so a
short one may land with the falling tone of a finished sentence. That is a
judgement for the ear, and raising this number is the dial for it.
"""
from __future__ import annotations

import functools

from livekit.agents import tokenize
from livekit.agents.tokenize import token_stream

# Always a break: sentence enders in both scripts, and the semicolon.
_HARD = ".!?。！？।॥;"

# A break only when it is punctuation rather than notation - see the docstring.
_SOFT = ",:，、"

# Long enough that a chunk is worth a request on its own, short enough that the
# first one arrives quickly. See the note on 12.7 characters per second.
DEFAULT_MIN_LEN = 14


def split_clauses(text: str, min_len: int = DEFAULT_MIN_LEN) -> list[tuple[str, int, int]]:
    """-> [(chunk, start, end)], breaking at clause boundaries.

    Offsets are into the original text, which is what lets livekit line the
    audio up with the transcript it shows.
    """
    out: list[tuple[str, int, int]] = []
    start = 0
    n = len(text)

    for i, ch in enumerate(text):
        if ch not in _HARD and ch not in _SOFT:
            continue
        if ch in _SOFT:
            prev = text[i - 1] if i else ""
            nxt = text[i + 1] if i + 1 < n else " "
            # 52,540 and 10:30 are one number and one time, not two pauses.
            if prev.isdigit() and nxt.isdigit():
                continue
            # A mark with no space after it is inside something - a decimal, a
            # URL, an abbreviation - rather than at the end of a clause.
            if not nxt.isspace() and i + 1 < n:
                continue
        end = i + 1
        chunk = text[start:end].strip()
        if len(chunk) < min_len:
            # Too short to send alone: hold it and let the next break carry it.
            continue
        out.append((chunk, start, end))
        start = end

    tail = text[start:].strip()
    if tail:
        out.append((tail, start, n))
    return out


class ClauseTokenizer(tokenize.SentenceTokenizer):
    """A SentenceTokenizer that also treats a clause as a place to stop.

    Given to `tts.StreamAdapter` in place of its default, which is blingfire
    and knows neither the danda nor a comma.
    """

    def __init__(self, *, min_len: int = DEFAULT_MIN_LEN,
                 ctx_len: int = 10) -> None:
        self._min_len = min_len
        self._ctx_len = ctx_len

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        return [c for c, _, _ in split_clauses(text, self._min_len)]

    def stream(self, *, language: str | None = None) -> tokenize.SentenceStream:
        # token_stream is not in livekit's public __all__, but it is what
        # basic.SentenceTokenizer uses for exactly this, and re-implementing
        # the buffering would be copying that file to avoid importing it.
        return token_stream.BufferedSentenceStream(
            tokenizer=functools.partial(split_clauses, min_len=self._min_len),
            min_token_len=self._min_len,
            min_ctx_len=self._ctx_len,
        )
