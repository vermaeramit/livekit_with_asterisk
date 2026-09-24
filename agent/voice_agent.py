"""
Sarvam STT -> OpenAI LLM -> Sarvam TTS, with Silero VAD, multilingual turn
detection, a two-layer knowledge base, and human handoff.

KB layer 1 - prompt: a small KB is injected whole; a large one contributes an
index of its headings. Costs nothing per turn.
KB layer 2 - tool: search_knowledge_base(), called only when layer 1 falls short.

Retrieving on EVERY turn was tried and rejected: 390-1244 ms per turn whether or
not the question needed it. It also removes the cross-script failure - when the
model writes the search query it writes English, and English scores 0.44-0.48
against this KB where the caller's raw Devanagari scored 0.13-0.20 and ranked
the wrong chunk.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import functools
import json
import logging
import os
import random
import re
import time
import zoneinfo

import aiohttp

from livekit import api
from livekit.agents import (
    Agent, AgentSession, JobContext, JobProcess, RoomInputOptions, RunContext,
    StopResponse, WorkerOptions, cli, function_tool, metrics,
)
# aliased: bare stt/tts/llm would shadow the local variables of the same name
from livekit.agents import llm as lk_llm, stt as lk_stt, tts as lk_tts
# google went with the hardcoded Gemini fallback - it was the only thing
# using it. Left out rather than left imported: the plugin pulls in the
# Google auth stack, and this module is imported in every job process.
from livekit.plugins import openai, sarvam, silero, soniox

import greeting_cache
import hours
import kokoro_tts
import prompt as prompt_mod
import tools as tools_mod
import providers as providers_mod
import tts_defaults

# NOTE: livekit.agents.inference.TurnDetector is the newer API, but its signature
# (base_url/api_key/conn_options) shows it can call a remote gateway. Turn
# detection runs on EVERY turn - a network hop there would wreck the endpointing
# budget. MultilingualModel is confirmed local (onnxruntime). Deprecated, pinned.
import warnings
warnings.filterwarnings("ignore", message=".*turn_detector.*deprecated.*")
from livekit.plugins.turn_detector.multilingual import MultilingualModel  # noqa: E402

logger = logging.getLogger("voice-agent")

CONFIG_NAME = os.getenv("AGENT_CONFIG", "default")
MIN_ENDPOINTING = float(os.getenv("MIN_ENDPOINTING_DELAY", "0.25"))

# How long a reply can be held back while the greeting plays - see
# KBAgent.on_user_turn_completed. Not a delay anybody hears: the gate lifts the
# moment the greeting finishes, about seven seconds in. This is only the ceiling
# for the case where that signal never arrives, and without a ceiling that case
# is an agent that answers nothing for the rest of the call.
GREETING_GATE_MAX_SEC = float(os.getenv("GREETING_GATE_MAX_SEC", "30"))
# The 4.0 default froze calls for 4s when a short closing scored below threshold.
MAX_ENDPOINTING = float(os.getenv("MAX_ENDPOINTING_DELAY", "1.5"))
# How long we will wait for the STT to call a transcript final once the words
# have stopped arriving. 0 disables it and restores the old behaviour exactly.
#
# Soniox will not send its end token until it agrees the caller has stopped, and
# on a line carrying constant low noise it may not agree for a very long time -
# 15926 ms on call 342, where the caller trailed off with "लेकिन।". Its own
# max_endpoint_delay_ms cannot help: that bounds the wait AFTER cessation is
# detected, so it never applies to the case that hurts.
STT_FINAL_CEILING = float(os.getenv("STT_FINAL_CEILING_MS", "2000")) / 1000

# Run TTS before the turn is confirmed, not just the LLM.
#
# Preemptive generation is already ON - the library defaults it to True and we
# never turned it off - but only for the LLM. preemptive_tts is False by
# default, so the voice does not start until the turn is settled, and TTS is
# this system's worst layer: 732 ms p50 and 1741 ms p95 measured over a week,
# against 241 ms documented on the provider we no longer use.
#
# The cost is not symmetric with the LLM's. A discarded preemptive LLM call is
# cheap now - the gateway model is a twentieth of the old price - but discarded
# TTS is Soniox characters at full rate, on the layer we are NOT saving on. So
# this is a flag: one day on, then read tts_ttfb and the Soniox bill together.
PREEMPTIVE_TTS = os.getenv("PREEMPTIVE_TTS", "0") == "1"

# Where our own text-to-speech answers, e.g. http://10.130.9.248:8880/v1.
#
# No default on purpose. Every other provider is at a name that means the same
# thing from anywhere; this is a LAN address that differs between servers, and a
# hardcoded one already cost this project two days of production calls landing
# on the development box (REPLICA.md). Unset and configured means a clear error
# naming this variable, which is better than reaching the wrong machine.
KOKORO_URL = os.getenv("KOKORO_URL", "").strip()

# Providers with no account behind them. Every other one is somebody else's
# service and cannot be used without a key; this one is a box we own, and
# demanding a credential for it would mean inventing a fiction to store. Both
# the key check at the start of a call and the fallback check read this.
KEYLESS = ("kokoro",)

# How long Silero waits, after the caller stops making sound, before saying the
# speech has ended. Nothing downstream can start until it does.
#
# The plugin's default is 0.55 and silero.VAD.load() was called with no
# arguments at all, so it has never been chosen. It shows up in the data as a
# floor rather than a measurement: on call 583 eou was 577 ms on nine separate
# turns while stt underneath it ranged from 297 to 652. 550 + ~27 ms of
# detector, every turn, whatever was said.
#
# Left at 0.55 so this change moves nothing. Lowering it is the experiment: the
# semantic turn detector is what makes that reasonable - Silero saying "stopped"
# only starts the question, and an unfinished sentence should still score as
# incomplete and wait, bounded by MIN/MAX_ENDPOINTING.
#
# The risk worth watching is not people being cut off - it is the STT being
# flushed sooner on a half-finished sentence, so the detector scores a
# transcript that was never going to be complete.
VAD_MIN_SILENCE = float(os.getenv("VAD_MIN_SILENCE", "0.55"))
# A knowledge-base hit below this is recorded as a gap even though it was used.
# Above kb_min_score, so it catches the band where an answer is technically
# grounded and practically a guess.
GAP_WEAK_BELOW = float(os.getenv("GAP_WEAK_BELOW", "0.45"))


def prewarm(proc: JobProcess):
    """Runs in a spawned process BEFORE it is given a call.

    MultilingualModel does NOT belong here - it needs a job context to reach the
    inference executor process, and loading it in prewarm crashes the worker.

    Everything else that costs measurable time on first use does belong here,
    because a job process handles exactly one call and then exits. Anything
    imported lazily inside entrypoint is therefore imported while a caller is
    listening to silence, once per caller, forever.

    That is not hypothetical: `import kb` sat inside build_instructions and cost
    1154 ms of every call's setup. kb pulls in pymupdf4llm (PyMuPDF, a large C
    extension used only for PDF INGESTION - never during a call), tiktoken, the
    OpenAI SDK, and at module scope loads the cl100k_base vocabulary. None of it
    is needed to answer a phone; all of it was being loaded to do so.

    Failures are logged and swallowed: a process that cannot pre-import still
    works, it just pays the cost later - which is exactly where it was before.
    """
    t = time.perf_counter()
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=VAD_MIN_SILENCE)
    vad_ms = (time.perf_counter() - t) * 1000

    t = time.perf_counter()
    try:
        import greeting_cache  # noqa: F401
        import kb  # noqa: F401  - imported for its side effect: being imported
        import store  # noqa: F401
    except Exception:
        logger.exception("prewarm imports failed - the first call in this "
                         "process will pay for them instead")
    imports_ms = (time.perf_counter() - t) * 1000

    logger.info("prewarm complete: VAD %.0f ms (min_silence=%.2fs), imports %.0f ms",
                vad_ms, VAD_MIN_SILENCE, imports_ms)


def _stt_kwargs(cfg):
    """Sarvam STT options, tunable from env.

    Sarvam's SERVER-side VAD decides END_SPEECH and only then does the plugin
    flush - measured ~700 ms behind the local Silero VAD. high_vad_sensitivity
    removes almost all of it. Note saarika:* has supports_vad_params=False, so
    negative_frames_count and friends are silently dropped; flush_signal measured
    as a no-op.
    """
    # No SARVAM_STT_MODEL here any more. It used to come first in this chain,
    # so a value in the server's .env silently beat the console - somebody could
    # change the model, see it save, and every call would keep using the old one
    # with nothing anywhere to say why. The console owns this field; the env var
    # predates it owning anything.
    kw = {"language": cfg.language,
          "model": cfg.stt_model or "saarika:v2.5"}
    if os.getenv("SARVAM_STT_MODE"):
        kw["mode"] = os.getenv("SARVAM_STT_MODE")
    if os.getenv("SARVAM_HIGH_VAD"):
        kw["high_vad_sensitivity"] = os.getenv("SARVAM_HIGH_VAD") == "1"
    if os.getenv("SARVAM_FLUSH_SIGNAL"):
        kw["flush_signal"] = os.getenv("SARVAM_FLUSH_SIGNAL") == "1"
    for env, key, cast in (("SARVAM_NEG_FRAMES", "negative_frames_count", int),
                           ("SARVAM_NEG_WINDOW", "negative_frames_window", int),
                           ("SARVAM_NEG_THRESH", "negative_speech_threshold", float),
                           ("SARVAM_POS_THRESH", "positive_speech_threshold", float),
                           ("SARVAM_MIN_SPEECH", "min_speech_frames", int)):
        if os.getenv(env):
            kw[key] = cast(os.getenv(env))
    return kw


def _tts_kwargs(cfg):
    kw = {"target_language_code": cfg.language,
          "model": cfg.tts_model or tts_defaults.SARVAM_MODEL}
    # SARVAM_TTS_VOICE removed for the same reason as SARVAM_STT_MODEL above:
    # an env var that beats the console is a console that can lie.
    voice = cfg.tts_voice
    if voice:
        kw["speaker"] = voice
    return kw


# ── dialler context ─────────────────────────────────────────────────────────
# The dialler sends these as IAX2 variables; Asterisk puts them on the INVITE
# (see [recsetup]) and livekit-sip maps them to participant attributes via the
# trunk's headers_to_attributes.
#
# Split deliberately. Only the conversational half reaches the model: a model
# handed a lead id will, sooner or later, read it out to the caller. The
# identifiers exist for correlation with the dialler's CRM and go to the
# database only.
# One definition, in prompt.py, because the same set governs BOTH ways a dialler
# field can reach a caller: this context message, and {{placeholder}} substitution
# into spoken strings. They were curated separately, and the second one was not
# curated at all - anything the dialler sent would render, so {{lead_id}} in a
# greeting read a CRM identifier out loud.
#
# prompt.py rather than here because it imports nothing, so the console can read
# the same list and warn before a placeholder like that is ever saved.
#
# And since 22 Sep 2026 even those three reach the model only when the campaign's
# prompt asks for them by {{placeholder}} - prompt.caller_details.

# There is no list of record-only fields, deliberately. It is EVERYTHING ELSE -
# lead_id, sr_id, call_unique, and whatever the dialler adds next. A tuple naming
# four of them stood here and was read by nothing; it would have gone stale the
# first time they added a field, while looking authoritative.


def _dialler_attrs(participant) -> dict[str, str]:
    """Everything the dialler sent, empties dropped.

    ⚠️ LiveKit's docs say headers_to_attributes populates asynchronously, so
    these may not be present the moment the participant joins. This is read late
    - after the config load and the first database writes - which in practice
    leaves them time to arrive. It is NOT waited for: adding a delay to every
    call to cover a case that has not yet been seen would cost more than it
    saves. The log line below is how we would find out; if it starts reporting
    nothing on calls that should have context, the fix is
    lk.sip.GetRemoteHeaders rather than a longer sleep.
    """
    if participant is None:
        return {}
    attrs = participant.attributes or {}
    # EVERY dialer.* attribute, not a fixed list. The dialler adds fields
    # without telling anyone - they added seven at once - and a field that
    # arrives but is not on an allowlist is silently thrown away, which is
    # indistinguishable from the dialler never sending it.
    #
    # This is the STORAGE side only. What reaches the model stays curated - see
    # prompt.caller_details: a model handed a lead id will eventually read it
    # out to the caller, and that must not become automatic.
    return {k: v for k, raw in attrs.items()
            if k.startswith("dialer.") and (v := (raw or "").strip())}


def _caller_context(dialler: dict[str, str], instructions: str):
    """-> (a ChatContext with the caller details the prompt asks for, or None;
           the dialer.* keys that were given).

    Only fields the campaign's prompt uses as {{cus_name}}, {{modalname}},
    {{calltype}} - see prompt.caller_details for the rule and why.

    A SEPARATE message, never appended to `instructions`. The instructions are
    the cacheable prefix - byte-identical across every call on a campaign, which
    is what earns OpenAI's prompt cache (measured 1198 ms cold against 805 ms
    warm). Putting a caller's name into them would make every call's prefix
    unique and the cache would never hit again, silently.
    """
    given, body = prompt_mod.caller_details(instructions, dialler)
    if not body:
        return None, given
    c = lk_llm.ChatContext.empty()
    c.add_message(role="system", content=body)
    return c, given


# Substituting dialler context into spoken strings lives in prompt.py, which
# the admin API can import - it has no livekit in it. The chat tester has to
# render a greeting exactly as a call would, and a second copy of the rules
# about defaults after the pipe would be a second copy to get wrong.
_render = prompt_mod.render_spoken


def _sip_attr(participant, *keys):
    for k in keys:
        v = (participant.attributes or {}).get(k)
        if v:
            return v
    return None


def _api_url() -> str:
    """The agent connects over ws://; the REST API needs http://."""
    u = os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880")
    return u.replace("wss://", "https://").replace("ws://", "http://")


def _tool_name(tool) -> str:
    """The name a livekit function tool is exposed to the model under.

    Not simply `tool.name`. A method decorated with @function_tool is still a
    function; its name lives in the tool info the decorator attaches, and
    reading `.name` off it returns nothing at all - which is how a filter meant
    to remove one tool silently removed none.
    """
    info = getattr(tool, "__livekit_tool_info__", None)
    for candidate in (getattr(info, "name", None),
                      getattr(tool, "name", None),
                      getattr(tool, "__name__", None)):
        if candidate:
            return str(candidate)
    return ""


def _audio_output_state(session) -> str:
    """'playing', 'paused' or 'unknown' - for the log, never for a decision.

    livekit pauses the room output when a caller starts speaking over a speech
    that has not begun yet, and resumes it on a timer. Whether it is paused at
    a given moment is not exposed, so this reads the room output's own flag
    down the chain. Private, and therefore allowed to answer 'unknown'.
    """
    try:
        out = getattr(session.output, "audio", None)
        while out is not None:
            flag = getattr(out, "_playback_enabled", None)
            if flag is not None:
                return "playing" if flag.is_set() else "paused"
            out = getattr(out, "next_in_chain", None)
    except Exception:
        pass
    return "unknown"


async def _danda_to_stop(text):
    """End Hindi sentences in a way the sentence splitters understand.

    THE ~1 SECOND NOBODY COULD ACCOUNT FOR. Across 46 measured turns the caller
    waited 2558 ms at the median while turn detection, the model's first token
    and the TTS's first audio came to 1596 ms between them.

    livekit's splitters end a sentence on [.!?。！？] and neither knows the
    Devanagari danda. Measured on .243, both of them - the basic one Sarvam
    falls back to and the blingfire one Soniox uses - return ONE sentence for a
    three-sentence Hindi answer. So a plugin has nothing complete to send until
    the model has written the entire reply, and the caller waits for all of it
    rather than for the first sentence. Every TTS_STREAM line shows it: the
    first audio arrives 100-500 ms AFTER the text finished, never during.

    A full stop is the same instruction to a TTS as a danda, so this only
    changes where the splitter cuts. The audio branch only - the transcript
    keeps the danda, as it keeps the markers stripped above it.
    """
    async for chunk in text:
        yield chunk.replace("।", ".").replace("॥", ".")


def _wait_and_timeline(agent, role: str, m: dict) -> tuple[int | None, list | None]:
    """-> (the caller's wait for this speech in ms, what filled it) or (None, None).

    `m` is the conversation item's livekit metrics. A caller turn only updates
    the state on `agent`; an agent speech gets a wait when it is the first thing
    said after a caller turn - livekit's own e2e_latency where it gave one, the
    same measure taken here where it did not.
    """
    wait_ms = None
    timeline = None
    if role == "user":
        agent.awaiting_answer = True
        if m.get("stopped_speaking_at"):
            agent.caller_stopped_at = m["stopped_speaking_at"]
        elif agent.user_quiet_at:
            # livekit had no end of speech for this turn. Call 627, turn
            # 5: the second half of a caller speaking in two pieces,
            # eou=0 stt=0, and its answer came back with no wait at all -
            # on the slowest turn of the call. VAD's own "stopped" lands
            # VAD_MIN_SILENCE after the last word, so that is taken off.
            agent.caller_stopped_at = agent.user_quiet_at - VAD_MIN_SILENCE
        if agent.caller_stopped_at:
            # Anything from before this caller turn belonged to a turn
            # that never got an answer, and explains nothing about the next.
            agent.heard[:] = [e for e in agent.heard
                              if e["start"] >= agent.caller_stopped_at - 0.05]
    elif role == "assistant":
        t0 = agent.caller_stopped_at
        wait = m.get("e2e_latency")
        if wait is None and agent.awaiting_answer and t0 and m.get("started_speaking_at"):
            # The same measure, taken here where livekit gives none: this
            # speech's first audio minus the caller's last word. livekit
            # never sets e2e on say() - the transfer message after a
            # caller asks for a person, call 627 turn 10 - nor on a turn
            # it had no end of speech for. Only the FIRST speech after a
            # caller turn: the greeting and the silence prompts answer
            # nobody, and must not look like slow answers.
            wait = m["started_speaking_at"] - t0
        if wait is not None and wait >= 0:
            wait_ms = int(wait * 1000)
            agent.awaiting_answer = False
            if t0:
                timeline = [
                    {"kind": e["kind"], "label": e["label"],
                     "at_ms": int((e["start"] - t0) * 1000),
                     "ms": int(((e["end"] or time.time()) - e["start"]) * 1000)}
                    for e in agent.heard if e["start"] >= t0 - 0.05] or None
            agent.heard.clear()
    return wait_ms, timeline


def _lookup_filler_s(cfg) -> float:
    """How long a lookup may run before the caller is told something.

    The campaign's own number, falling back to the environment default for a row
    written before the column existed.
    """
    ms = getattr(cfg, "lookup_filler_after_ms", None)
    return (ms / 1000) if ms else tools_mod.FILLER_AFTER_S


async def _warm_fillers(cfg, tts) -> None:
    """Render the acknowledgements once, so every later turn plays them instantly.

    Run after the greeting, when the caller is answering it and the TTS is idle -
    the call that finds the cache empty pays nothing it would not have paid
    anyway, and every call after it, on any worker, reads them from disk.

    The cache key is the text AND the voice, so changing either re-renders on its
    own; there is nothing to clear. See greeting_cache.
    """
    for raw in (getattr(cfg, "reply_filler_lines", None) or []):
        line = (raw or "").strip()
        if not line:
            continue
        path = greeting_cache.path_for(line, cfg.tts_provider, cfg.tts_model,
                                       cfg.tts_voice)
        if not path.exists():
            await greeting_cache.store(tts, line, path)


class KBAgent(Agent):
    def __init__(self, instructions: str, cfg, kb_mode: str, room, keys: dict,
                 chat_ctx=None, extra_tools=None):
        # instructions stay byte-identical per campaign - that is what OpenAI's
        # prompt cache keys on. Per-call context arrives as chat_ctx, AFTER the
        # cacheable prefix, never inside it.
        #
        # Tool DEFINITIONS are fine in the cached part: they are per-campaign and
        # identical across calls. Only their arguments differ, and those are not
        # in the prefix.
        super().__init__(
            instructions=instructions,
            **({"chat_ctx": chat_ctx} if chat_ctx else {}),
            **({"tools": list(extra_tools)} if extra_tools else {}),
        )
        self.cfg = cfg
        # Carried so the KB tool embeds its query on the client's key too. The
        # search path is easy to forget - it is billed per turn, not per upload.
        self.keys = keys
        self.kb_mode = kb_mode
        self.room = room
        self.tool_calls = 0
        # Set by the entrypoint once the row exists. A gap is worth recording
        # even without it - the question is the point, not the call - so this
        # stays None-tolerant rather than becoming a constructor argument that
        # has to be threaded through every path that builds an agent.
        self.call_id: int | None = None
        # Set when a search had to fall back to lexical because the embedding
        # could not be made. Kept rather than written on the spot: a DB insert
        # in the middle of a turn is latency the caller pays for, and this is
        # already a degraded call. Written once, at the end, with everything
        # else - see the call_errors insert.
        self.kb_degraded: str | None = None
        self.last_kb_ms = 0
        # Which chunks answered the current turn, best score first. Cleared when
        # the turn is written down, so it never carries into the next one.
        #
        # The console has always been able to show this - the endpoint resolves
        # ids to a filename, heading and page, and the turn renders them with
        # their scores. Nothing ever filled the column, so the section stayed
        # hidden and the whole path looked like it did not exist.
        self.last_kb_hits: list[tuple[int, float]] = []
        self.transferred: tuple[str, str] | None = None
        self.turn_count = 0
        self.prompt_tokens = 0
        self.limit_hit: str | None = None
        # False until the greeting has been heard in FULL - see
        # on_user_turn_completed.
        #
        # Starts False here in the constructor, not just before the greeting is
        # spoken. The agent is built before session.start, and session.start is
        # the moment the caller's audio begins arriving: a flag set any later
        # leaves a window in which a caller who says "hello" first gets an
        # answer to it instead of the greeting - the bug this exists to close.
        self.greeting_done = False
        # Set by the entrypoint once the session exists. The agent is built
        # first, so this cannot be a constructor argument.
        self.session_ref = None
        # What happened between the caller's last word and the answer: fillers
        # heard and lookups run, stamped in wall-clock time (livekit's own
        # timestamps are time.time() too) and drained into the answer's
        # timeline in _on_item. filler_ids names the speeches that are fillers,
        # so the moment one starts playing can be recognised.
        self.filler_ids: dict[str, str] = {}
        self.heard: list[dict] = []
        self.caller_stopped_at: float | None = None
        # Set by a caller turn, cleared by the first speech after it: the one
        # speech a caller was actually waiting for.
        self.awaiting_answer = False
        # When VAD last said the caller stopped - the fallback when livekit has
        # no end of speech for a turn.
        self.user_quiet_at: float | None = None
        self._greeting_gate_since = time.monotonic()
        # Set when the model writes the end-of-call marker. Acted on after the
        # sentence carrying it has finished playing, never during.
        self.end_requested = False
        self.transfer_requested = False
        # Set when the call was ended deliberately by something other than a
        # limit: today only the silence handler. Feeds calls.end_reason, so
        # "nobody ever spoke" is countable separately from "it finished".
        self.ended_by: str | None = None
        # How many times the caller has actually said something. The transfer
        # gate is measured against this, not against a boolean - see below.
        self.user_turns = 0
        # The value of user_turns when the caller was asked to confirm a
        # handoff, or None if they have not been asked.
        #
        # A boolean was not enough. Given both a tool and a marker for the same
        # job, the model used BOTH in one response: the tool asked for
        # confirmation and set the flag, and the marker - arriving milliseconds
        # later, before the caller could draw breath - found the flag already
        # set and transferred. The confirmation was satisfied by the agent
        # talking to itself.
        #
        # Requiring the caller to have spoken SINCE being asked is a gate that
        # holds however many routes exist, because only the caller can move it.
        # Characters of final transcript per detected language, from Soniox's
        # own identification. Written to the call row at the end.
        self.language_chars: dict[str, int] = {}
        self.transfer_asked_at: int | None = None
        # Why a handoff was turned down, for the call row. The interesting list
        # this makes is not failed transfers - it is callers who wanted a
        # person at 9pm, which is a callback list.
        self.transfer_refused: str | None = None

        # One route, not two. The double-fire above is what having both looks
        # like from the caller's side: three utterances for one handoff, and a
        # confirmation they were never given a chance to answer.
        if cfg.transfer_marker and cfg.transfer_enabled:
            try:
                kept = [t for t in self.tools
                        if _tool_name(t) != "transfer_to_human"]
                self.update_tools(kept)
                # Logged, not assumed. The first version of this filtered on
                # `t.name`, which does not exist on a decorated tool - so it
                # matched nothing, removed nothing, raised nothing, and the
                # model went on calling the tool. Silent no-ops are why this
                # line exists.
                logger.info("transfer marker set - tools now: %s",
                            ", ".join(_tool_name(t) for t in self.tools) or "none")
            except Exception:
                logger.warning(
                    "could not remove the transfer tool - the marker and the "
                    "tool are both live, and the model may use either")

    # ────────────────────── end of call ──────────────────────

    async def on_user_turn_completed(self, turn_ctx, new_message):
        """Say nothing back until the greeting has finished playing.

        A caller who spoke first, or over the greeting, used to get an answer to
        "hello" and lose the greeting. The greeting itself is now uninterruptible
        (see _speak_greeting), which stops it being cut; this stops a reply being
        generated around it. Both are needed: a turn can COMPLETE before the
        greeting is even scheduled, because session.start opens the caller's
        audio before the greeting is queued and preemptive generation starts the
        model on whatever it hears.

        StopResponse is livekit's own way to decline a turn - no reply, no
        speech. What the caller said is not answered; the greeting ends in a
        question, so they are asked to speak again anyway.

        THE FAILURE THIS MUST NOT HAVE is a flag that never clears: an agent
        silent for the whole call. So the gate has a ceiling. Past
        GREETING_GATE_MAX_SEC it opens regardless, and says so in the log.
        """
        if not self.greeting_done:
            waited = time.monotonic() - self._greeting_gate_since
            if waited < GREETING_GATE_MAX_SEC:
                logger.info("greeting still playing - not replying to %r",
                            (getattr(new_message, "text_content", None) or "")[:60])
                raise StopResponse()
            logger.warning("greeting never reported finishing after %.0fs - "
                           "replying anyway rather than going silent", waited)
            self.greeting_done = True
        self._start_reply_filler()
        await super().on_user_turn_completed(turn_ctx, new_message)

    def _start_reply_filler(self) -> None:
        """Make a short sound, so the caller is not answered by silence.

        Measured over 2,350 turns: the model's first token lands at 931 ms and
        its first audio at 1555 ms. That is the pause after everything a caller
        says. It does not cover the pause before it - turn detection has to
        decide they have finished, and talking over somebody who has not is a
        worse failure than making them wait.

        QUEUED HERE, BEFORE super() ASKS FOR THE REPLY, and that ordering is the
        whole of it. livekit creates the reply's speech handle the moment the
        turn ends, and the speech queue plays in the order handles were made. A
        say() issued 300 ms later therefore lands BEHIND the reply and is heard
        after the answer - which is exactly what the first version of this did
        on a live call: "...जी…" once the agent had already finished speaking.

        So the wait cannot be a sleep before say(). It is a silence at the front
        of the audio instead - see _filler_audio - which leaves the handle at the
        head of the queue while still giving the caller a beat to carry on
        speaking if they had not finished. If they do, this is interruptible and
        nothing was said.

        The consequence, stated plainly: the sound is now made on EVERY turn
        rather than only on slow ones. Nothing here can know yet whether the
        reply will be fast, because the decision has to be taken before the
        reply has even been asked for. It costs the caller something only when
        the answer would have arrived sooner than this finishes - about 700 ms
        against a median of 1555.

        Never added to the chat context: the model must not see itself having
        spoken - it would answer as though it had already acknowledged - and the
        transcript must not gain a turn that carries no answer.
        """
        session = self.session_ref
        lines = [s.strip() for s in (getattr(self.cfg, "reply_filler_lines", None) or [])
                 if (s or "").strip()]
        if not getattr(self.cfg, "reply_filler_enabled", False) or not lines or session is None:
            return

        # Only lines that are already rendered. A cold cache means the first call
        # on a campaign stays silent exactly as it does today, rather than paying
        # 650 ms of TTS in front of the answer to say "जी…" - _warm_fillers is
        # filling it in the background and the next call will have it.
        ready = [(ln, p) for ln in lines
                 if (p := greeting_cache.path_for(ln, self.cfg.tts_provider,
                                                  self.cfg.tts_model,
                                                  self.cfg.tts_voice)).exists()]
        if not ready:
            return
        line, path = random.choice(ready)
        wait = (getattr(self.cfg, "reply_filler_after_ms", None) or 300) / 1000

        try:
            h = session.say(line, audio=self._filler_audio(path, wait),
                            allow_interruptions=True, add_to_chat_ctx=False)
            logger.info("reply filler %s: %r after %dms", h.id, line, int(wait * 1000))
            self.filler_ids[h.id] = line
        except Exception:
            logger.exception("reply filler could not be queued")

    async def _filler_audio(self, path, wait: float):
        """The cached sound, with the configured wait in front of it as silence.

        The delay lives here rather than before say() because the handle has to
        exist before the reply's does. Yielding nothing at all is a legitimate
        outcome - the handle simply ends, and the reply follows immediately.
        """
        try:
            frames = greeting_cache.frames(path)
            if frames is None:
                return
            await asyncio.sleep(wait)
            async for frame in frames:
                yield frame
        except asyncio.CancelledError:
            raise       # the caller carried on speaking; let the interrupt land
        except Exception:
            logger.exception("reply filler audio failed")

    def _markers(self) -> dict[str, str]:
        """{marker text -> the flag it sets}, empties dropped."""
        out = {}
        if self.cfg.end_call_marker:
            out[self.cfg.end_call_marker] = "end_requested"
        if self.cfg.transfer_marker and self.cfg.transfer_enabled:
            out[self.cfg.transfer_marker] = "transfer_requested"
        return out

    async def stt_node(self, audio, model_settings):
        """One place to watch every transcript, whatever route it took here.

        _stt_events has five separate yields across two paths, and a language
        tap on each of them is a tap somebody forgets to add to the sixth. One
        wrapper cannot be forgotten.
        """
        async for ev in self._stt_events(audio, model_settings):
            self._note_language(ev)
            yield ev

    def _note_language(self, ev) -> None:
        """Count characters per language, from what Soniox already tells us.

        `enable_language_identification` defaults to true in the plugin and we
        never turned it off, so every transcript has arrived carrying a
        detected language since the day Soniox went in. Nothing read it. The
        language shown against a call was `cfg.language` - the campaign's
        setting, which is the same for every call and says nothing about what
        the caller actually spoke.

        Characters rather than turns, because these calls are Hinglish and the
        interesting figure is the MIX. Counting turns would score a two-word
        English aside the same as a full Hindi sentence.

        Finals only. Interims arrive continuously and revise themselves, so
        counting them would weight the beginning of every sentence by however
        many times it was re-sent.
        """
        if getattr(ev, "type", None) != lk_stt.SpeechEventType.FINAL_TRANSCRIPT:
            return
        alts = getattr(ev, "alternatives", None) or []
        if not alts:
            return
        lang = (getattr(alts[0], "language", "") or "").strip().lower()
        text = getattr(alts[0], "text", "") or ""
        if not lang or not text:
            return
        # "hi-IN" and "hi" are the same language and must not become two rows.
        lang = lang.split("-")[0]
        self.language_chars[lang] = self.language_chars.get(lang, 0) + len(text)

    async def _stt_events(self, audio, model_settings):
        """Hold our own ceiling on how long a transcript may stay provisional.

        The plugin streams interim transcripts continuously and withholds only
        the FINAL, which it sends when the provider decides the caller has
        stopped. Everything downstream - the turn, the reply, the whole call -
        waits on that decision, and on call 342 it took 15926 ms while the
        finished sentence sat in our hands the entire time.

        No provider setting fixes that: Soniox's max_endpoint_delay_ms bounds
        the wait after cessation is detected, and the failure is that cessation
        is never detected. A phone line is rarely silent enough to convince it.

        So the words stop arriving, a clock runs, and if nothing has been called
        final by the time it expires we promote the last interim ourselves. The
        provider's own final turns up later and is dropped, because the caller
        has already been answered.

        The promoted event is a copy of that interim with only its type changed,
        so language, timings and request_id are exactly what the plugin set. A
        hand-built event would differ in some field nobody would think to check
        until something downstream tripped over it.

        Set STT_FINAL_CEILING_MS=0 to turn all of this off and get the stock
        behaviour back, unchanged, in one restart.
        """
        default = Agent.default.stt_node(self, audio, model_settings)
        if STT_FINAL_CEILING <= 0:
            async for ev in default:
                yield ev
            return

        def text_of(ev) -> str:
            alts = getattr(ev, "alternatives", None) or []
            return (getattr(alts[0], "text", "") or "") if alts else ""

        def norm(t: str) -> str:
            return " ".join(t.split()).strip().lower()

        # Read on a task rather than inline: the ceiling has to be able to fire
        # while nothing is arriving, and timing out an async generator's
        # __anext__ cancels it. A queue lets the clock run without touching the
        # stream that feeds it.
        queue: asyncio.Queue = asyncio.Queue()

        async def pump() -> None:
            try:
                async for ev in default:
                    await queue.put(ev)
            except Exception:
                logger.exception("stt stream failed")
            finally:
                await queue.put(None)

        task = asyncio.create_task(pump())
        pending_interim = None
        interim_at = 0.0
        last_text = ""
        promoted = ""

        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if (pending_interim is not None
                            and time.monotonic() - interim_at > STT_FINAL_CEILING):
                        try:
                            final = dataclasses.replace(
                                pending_interim,
                                type=lk_stt.SpeechEventType.FINAL_TRANSCRIPT)
                        except Exception:
                            # Not a dataclass after some future upgrade. Give up
                            # on the ceiling rather than take the STT down with
                            # it - a slow call beats a deaf one.
                            logger.exception("cannot promote an interim - "
                                             "ceiling disabled for this call")
                            pending_interim = None
                            continue
                        promoted = text_of(pending_interim)
                        logger.info("STT ceiling %.1fs reached - answering on the "
                                    "interim: %r", STT_FINAL_CEILING, promoted[:70])
                        pending_interim = None
                        yield final
                    continue

                if ev is None:
                    break

                if ev.type == lk_stt.SpeechEventType.INTERIM_TRANSCRIPT:
                    txt = text_of(ev)
                    # Identical to what we already answered on: the provider is
                    # still repeating the same segment. Passing it would reopen
                    # a turn that has been dealt with.
                    if promoted and norm(txt) == norm(promoted):
                        continue
                    if txt and txt != last_text:
                        # New words. The caller is still going, so the clock
                        # restarts - it measures silence, not elapsed time.
                        last_text = txt
                        pending_interim = ev
                        interim_at = time.monotonic()
                    yield ev
                    continue

                if ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT:
                    txt = text_of(ev)
                    pending_interim = None
                    last_text = ""
                    if promoted and norm(txt) == norm(promoted):
                        logger.info("STT late final dropped - already answered")
                        promoted = ""
                        continue
                    # Longer than what we promoted means the caller carried on
                    # talking. Let it through: a repeated sentence is a nuisance
                    # and a lost one is not recoverable.
                    promoted = ""
                    yield ev
                    continue

                yield ev
        finally:
            # Cancelled, not awaited. Awaiting inside an async generator's
            # finally while it is being closed is how you get "async generator
            # ignored GeneratorExit"; the cancel is enough to end the pump.
            task.cancel()

    async def tts_node(self, text, model_settings):
        """Measure how an answer's audio arrives. Passes every frame through untouched.

        Call 621 (22 Sep 2026): a 15-word answer was "speaking" for 17.5 s, and
        10.0 s of it was dead silence in 18 holes - measured in the recording,
        up to 2.2 s long, in the middle of sentences. The greeting and the
        fillers in the same call, played from disk, had none. CPU was idle and
        nothing interrupted the agent. So the audio reached the output slower
        than it plays - but whether the TTS was slow, or the model's text was
        still trickling in, nothing logged could say.

        This says it. Playback is modelled from the first frame in real time:
        a frame that arrives after the audio already received has run out is a
        stall, of exactly that gap - the holes heard on the call. Alongside it,
        when the model's text had fully arrived, relative to the first audio.
        Stalls after that moment are the TTS; stalls before it may be waiting
        on the text. No stalls here but holes in the recording would put the
        fault downstream of the TTS instead.
        """
        spoken: list[str] = []
        text_done: list[float] = []
        started = time.monotonic()

        async def watched_text():
            async for chunk in text:
                spoken.append(chunk)
                yield chunk
            text_done.append(time.monotonic())

        first = run_out = None
        audio_s = 0.0
        stalls: list[tuple[float, float]] = []    # (s after first audio, length)
        try:
            async for frame in self._tts_frames(watched_text(), model_settings):
                now = time.monotonic()
                if first is None:
                    first = run_out = now
                elif now > run_out + 0.02:
                    stalls.append((run_out - first, now - run_out))
                    run_out = now
                run_out += frame.duration
                audio_s += frame.duration
                yield frame
        finally:
            # Logging only, and nothing awaited: this runs while the generator
            # is being closed, including when the caller barges in.
            if first is not None:
                label = " ".join("".join(spoken).split())[:40]
                logger.info(
                    "TTS_STREAM %r audio=%.1fs stalls=%d stalled=%.1fs worst=%dms "
                    "first_audio=%dms text_complete=%s",
                    label, audio_s, len(stalls), sum(g for _, g in stalls),
                    int(max((g for _, g in stalls), default=0) * 1000),
                    int((first - started) * 1000),
                    f"{int((text_done[0] - first) * 1000):+d}ms" if text_done else "never")
                if stalls:
                    logger.info("TTS_STALLS at+len(ms) %s", " ".join(
                        f"{int(at * 1000)}+{int(g * 1000)}" for at, g in stalls[:30]))

    async def _tts_frames(self, text, model_settings):
        """Strip control markers before anything is synthesised.

        A marker cannot simply be searched for in each chunk. An LLM streams its
        answer in pieces with no regard for token boundaries, so "[EOC]"
        routinely arrives as "[EO" then "C]" - and a naive filter passes both
        through, leaving the caller listening to "bracket E O C".

        So a tail is held back: anything that could still turn into a marker is
        buffered rather than spoken, and released once it is clear it will not.
        Costs at most a few characters of latency, which is inside a single TTS
        chunk. A real "[order]" in the text is untouched, because the held-back
        prefix is released the moment it stops matching.

        The ACTION is not taken here. This runs while audio is still being
        produced; hanging up or transferring now would cut off the sentence the
        marker was attached to. It only raises a flag - see call_watchdog.
        """
        markers = self._markers()
        if not markers:
            async for frame in Agent.default.tts_node(self, _danda_to_stop(text),
                                                      model_settings):
                yield frame
            return

        # Case-insensitive, because models do not respect the case they are
        # given. Asked for [TRANSFER], one wrote [Transfer] - which matched
        # nothing, so the marker was left in the text to be read aloud.
        lowered = {m.lower(): flag for m, flag in markers.items()}
        longest = max(len(m) for m in lowered)

        async def filtered():
            held = ""
            async for chunk in text:
                buf = held + chunk
                low = buf.lower()
                for marker, flag in lowered.items():
                    start = low.find(marker)
                    while start != -1:
                        setattr(self, flag, True)
                        buf = buf[:start] + buf[start + len(marker):]
                        low = buf.lower()
                        start = low.find(marker)
                # Keep back anything that is still a possible prefix of ANY
                # marker. Only the longest such suffix - holding more would
                # delay speech for no reason.
                held = ""
                for i in range(1, min(longest, len(buf)) + 1):
                    tail = buf[-i:].lower()
                    if any(m.startswith(tail) for m in lowered):
                        held = buf[-i:]
                buf = buf[:len(buf) - len(held)] if held else buf
                if buf:
                    yield buf
            # A partial marker at the very end was never a marker.
            if held:
                yield held

        async for frame in Agent.default.tts_node(self, _danda_to_stop(filtered()),
                                                  model_settings):
            yield frame

    # ────────────────────────── knowledge ──────────────────────────

    # This description used to read "use this ONLY when the REFERENCE
    # INFORMATION in your instructions does not answer the question". On a
    # knowledge base of any size the instructions carry titles, not text, and
    # there is no REFERENCE INFORMATION section at all - so the condition could
    # never be met and ONLY was a brake with nothing to release it.
    #
    # Call 538: thirteen turns about buying a motorcycle, zero searches, and an
    # answer about i3s that came from the model's training rather than from the
    # customer's documents. It was right by luck.
    #
    # Keep this docstring lean. It is not documentation - it is the tool
    # description sent to the model on every turn.
    @function_tool
    async def search_knowledge_base(self, context: RunContext, query: str) -> str:
        """Look up details in the company documents.

        Call this whenever the caller asks about a product, a price, a
        specification, a feature, an offer or a policy and the answer is not
        already written out in your instructions in full.

        A list of document titles is not an answer. If all you have is a title,
        search. Knowing that a document exists is not knowing what is in it.

        Args:
            query: A short search query in ENGLISH describing what to find, e.g.
                "cancellation policy refund", "baggage allowance". Always English,
                even when the caller speaks another language - the documents are
                in English and an English query matches them far better.
        """
        import kb
        import store
        self.tool_calls += 1
        t0 = time.perf_counter()

        # Same shape as a campaign tool's filler, and for the same reason: it
        # overlaps the search rather than being added in front of it, and it is
        # cancelled the instant the answer lands, so a fast search says nothing.
        #
        # This one earns its place more than the tool version did. A search now
        # costs 810-1860 ms and, with 108k tokens behind an index, runs on very
        # nearly every question the caller asks - that is a second of silence
        # each time, and silence is what makes a caller say "hello?".
        filler = ((getattr(self.cfg, "kb_filler_message", None) or "").strip()
                  if getattr(self.cfg, "kb_filler_enabled", True) else "")

        async def hold_on() -> None:
            try:
                await asyncio.sleep(_lookup_filler_s(self.cfg))
                # Kept out of the chat context on purpose. It is a noise made
                # while waiting, not something the agent said: the model should
                # not see itself having spoken, and the transcript should not
                # gain a turn that carries no answer.
                h = context.session.say(filler, allow_interruptions=True,
                                        add_to_chat_ctx=False)
                logger.info("kb filler %s: %r", h.id, filler)
                self.filler_ids[h.id] = filler
                await h
            except asyncio.CancelledError:
                pass        # the search answered first, which is the good case
            except Exception:
                logger.exception("kb filler failed")

        waiting = asyncio.create_task(hold_on()) if filler else None
        looked_from = time.time()
        try:
            hits = await kb.search(query, self.cfg.name,
                                   self.cfg.kb_top_k, self.cfg.kb_min_score,
                                   api_key=self.keys.get("openai"),
                                   on_degraded=self._kb_degraded)
        except Exception:
            logger.exception("kb search failed")
            return "The knowledge base is unavailable right now."
        finally:
            if waiting is not None:
                waiting.cancel()
            self.heard.append({"kind": "lookup", "label": "knowledge base search",
                               "start": looked_from, "end": time.time()})
        self.last_kb_ms = int((time.perf_counter() - t0) * 1000)
        # Kept per turn rather than per call: a model that searches twice for one
        # answer used both, and the reader wants to see both. Best score wins on
        # a repeat - the same chunk twice would also collide as a React key.
        best: dict[int, float] = {i: sc for i, sc in self.last_kb_hits}
        for h in hits:
            cid, score = h["id"], float(h["score"])
            if score > best.get(cid, -1.0):
                best[cid] = score
        self.last_kb_hits = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        logger.info("  TOOL search_knowledge_base(%r) -> %d hit(s) in %d ms  %s",
                    query, len(hits), self.last_kb_ms,
                    [f"{h['score']:.2f}" for h in hits])
        # What the caller wanted and did not get, written down so it can be
        # taught. Not awaited - it is a note about the call, and the caller is
        # waiting on the answer, not on the note.
        #
        # The query rather than the transcript, deliberately: it is already the
        # question distilled to what was being looked for, which is what someone
        # filling the gap needs to read.
        best = max((h["score"] for h in hits), default=None)
        if not hits:
            asyncio.create_task(store.record_gap(
                self.call_id, self.cfg.campaign_id, kind="kb_miss", query=query,
                detail="no chunk scored above %.2f" % self.cfg.kb_min_score))
        elif best is not None and best < GAP_WEAK_BELOW:
            # Found, but only just. RidgeMax MR came back at 0.34 against a 0.25
            # floor and the agent answered from a chunk that barely matched -
            # which reads exactly like a good answer until somebody checks it.
            asyncio.create_task(store.record_gap(
                self.call_id, self.cfg.campaign_id, kind="kb_weak", query=query,
                best_score=float(best),
                detail="best match %.2f" % best))

        if not hits:
            # Say it explicitly. Returning an empty string reads as permission to
            # answer from general knowledge - that is where invented phone numbers
            # come from.
            return ("No relevant information found in the documents. "
                    "Tell the caller you do not have that information.")
        return kb.format_context(hits)

    # ────────────────────────── handoff ──────────────────────────

    def _kb_degraded(self, message: str) -> None:
        """A search ran without its embedding leg. First one wins.

        The first is the one that says what broke; the ones after it are the
        same failure repeating, and a hundred identical rows would drown the
        alert rather than sharpen it.
        """
        if self.kb_degraded is None:
            self.kb_degraded = message

    @function_tool
    async def transfer_to_human(self, context: RunContext, reason: str) -> str:
        """Hand this call over to a human colleague.

        Use when the caller asks for a person, sounds frustrated, wants to
        complain, or asks something you cannot answer even after searching.

        Args:
            reason: A short note on why the handoff is needed, for the call log.
        """
        return await self.request_transfer(context.session, reason, context)

    async def request_transfer(self, session, reason: str, context=None) -> str:
        """The whole handoff flow, reachable from the tool AND the marker.

        One implementation on purpose: a campaign can drive handoff either way,
        and two copies of the confirmation gate is how one of them ends up
        without it.
        """
        if not self.cfg.transfer_enabled:
            return ("Transfer is disabled. Tell the caller to call back during "
                    "office hours.")

        # Before the confirmation gate, not after. Asking "shall I connect
        # you?" and then refusing once they say yes is worse than not offering:
        # the caller has agreed to something and then been turned down.
        #
        # Checked at the moment of the request rather than at the start of the
        # call, because that is when it is true. A call that connects at 18:25
        # and asks for a person at 18:35 is refused, and correctly - there is
        # nobody at the desk at 18:35.
        open_now, why = hours.is_open(self.cfg)
        if not open_now:
            self.transfer_refused = why
            message = hours.closed_message(self.cfg)
            logger.info("  TRANSFER(%r) refused - %s, next open %s",
                        reason, why, hours.next_open(self.cfg))
            try:
                handle = await session.say(message, allow_interruptions=True)
                await handle.wait_for_playout()
            except Exception:
                logger.exception("closed-hours message failed")
            return ("A human colleague is not available at this hour and the "
                    "caller has been told so. Do not offer to transfer again "
                    "on this call. Carry on helping them yourself.")

        # Ask first, and make the asking a state change here rather than an
        # argument the model supplies. A `confirmed: bool` parameter is a
        # parameter the model can set true on its very first call, which defeats
        # the whole point - the caller who says "no, wait" is already gone.
        #
        # Returning instead of transferring hands control back to the model,
        # which asks and waits. The caller's answer is a normal turn, after
        # which the model asks again - and by then the gate has moved.
        if self.cfg.transfer_confirm:
            if self.transfer_asked_at is None:
                self.transfer_asked_at = self.user_turns
                ask = (self.cfg.transfer_confirm_message
                       or "Main aapko ek sathi se jod rahi hoon. Theek hai?")
                logger.info("  TRANSFER(%r) -> asking the caller first", reason)
                try:
                    handle = await session.say(ask, allow_interruptions=True)
                    await handle.wait_for_playout()
                except Exception:
                    logger.exception("transfer confirmation prompt failed")
                return ("You have asked the caller to confirm. Wait for their "
                        "answer. If they agree, ask for the transfer again. If "
                        "they say no or want to continue, carry on helping them.")

            # Asked, but the caller has not spoken since. This is the case that
            # actually happened: a tool call asked and a marker in the same
            # response transferred, milliseconds apart, with the caller silent
            # throughout. Only the caller can move this counter.
            if self.user_turns <= self.transfer_asked_at:
                logger.info("  TRANSFER(%r) -> refused, the caller has not "
                            "answered yet", reason)
                return ("The caller has not answered yet. Wait for them to "
                        "reply before asking for the transfer again.")

        sip_identity = None
        for p in self.room.remote_participants.values():
            if _sip_attr(p, "sip.callID", "sip.phoneNumber") or p.identity.startswith("sip_"):
                sip_identity = p.identity
                break
        if not sip_identity:
            logger.error("transfer requested but no SIP participant in room")
            return "Transfer failed. Apologise and offer to take a callback number."

        # Speak the handoff line and let it finish BEFORE the REFER goes out.
        # Without the wait the caller gets abrupt silence and then a stranger.
        msg = self.cfg.transfer_message or "One moment, connecting you now."
        if context is not None:
            try:
                context.disallow_interruptions()
            except Exception:
                pass
        try:
            handle = await session.say(msg, allow_interruptions=False)
            await handle.wait_for_playout()
        except Exception:
            logger.exception("handoff announcement failed - transferring anyway")

        # Resolved once and used for the request, the log and the call row, so
        # what is recorded is what was actually asked for.
        target = _transfer_target(self.cfg)
        logger.info("  TRANSFER(%r) -> %s  participant=%s",
                    reason, target, sip_identity)
        lkapi = api.LiveKitAPI(url=_api_url(),
                               api_key=os.environ["LIVEKIT_API_KEY"],
                               api_secret=os.environ["LIVEKIT_API_SECRET"])
        try:
            await lkapi.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    participant_identity=sip_identity,
                    room_name=self.room.name,
                    transfer_to=target,
                    play_dialtone=False,
                ))
            self.transferred = (target, reason)
            logger.info("  TRANSFER OK -> %s", target)
            return "Transferred. Say nothing further."
        except Exception as e:
            logger.exception("transfer failed")
            return (f"Transfer failed ({type(e).__name__}). Apologise to the caller "
                    "and offer to take a callback number.")
        finally:
            await lkapi.aclose()


# ────────────────────────── provider stacks ──────────────────────────
# Chains chosen in Step 10b by measurement, not preference:
#
#   STT  Sarvam saarika:v2.5  -> OpenAI gpt-4o-mini-transcribe  (worse Indic)
#   TTS  Sarvam bulbul:v3     -> OpenAI gpt-4o-mini-tts         (+650ms, new voice)
#   LLM  OpenAI gpt-4.1-mini  -> Google gemini-flash-lite-latest (~no cost)
#
# All four Gemini TTS models were measured at a 3.5-15s TTFB floor and rejected.
# Do not revisit without new evidence that Google has changed something.
#
# Set PROVIDER_FALLBACK=0 to run on primaries alone - useful when benchmarking,
# because a fallback firing quietly changes what is being measured.

FALLBACK = os.getenv("PROVIDER_FALLBACK", "1") != "0"

# The defaults are tuned for transcription jobs, not phone calls: 10s on STT and
# 5s on LLM mean the caller sits in silence long past the point the call is lost.
# Our own p95 TTFT is ~900ms, so 3s is already generous.
ATTEMPT_TIMEOUT = float(os.getenv("FALLBACK_ATTEMPT_TIMEOUT", "3.0"))


# Every constructor below takes its key EXPLICITLY rather than letting the
# plugin read the environment. That is the whole point of per-client keys: the
# env still holds platform keys (KB embedding uses them), so a plugin left to
# find its own would silently bill the wrong account and nothing would look
# wrong.

# What each provider emits natively. The TTS FallbackAdapter resamples anything
# that does not match the rate it is given, so this is set from the PRIMARY -
# the common path then never resamples, and only a firing fallback pays for it.
_TTS_NATIVE_RATE = {"sarvam": 22050, "openai": 24000, "soniox": 24000,
                    # Kokoro's own output rate, and what its server reports.
                    "kokoro": 24000}


# Conservative, and ours rather than Soniox's - their published limit is not
# something this code has verified. An oversized context does not degrade, it
# fails the websocket handshake, and that takes the whole call with it. Erring
# low costs a few terms nobody was going to need.
_MAX_CONTEXT_TERMS = 150
_MAX_TERM_CHARS = 60


def _context_terms(cfg) -> list[str]:
    """The campaign's vocabulary, cleaned and capped.

    Cleaned here as well as in the console because this is the last point
    before the wire: a row edited by hand in psql, or written by an older
    console, reaches the provider through here and nowhere else.
    """
    raw = getattr(cfg, "stt_context_terms", None) or []
    out: list[str] = []
    seen = set()
    for term in raw:
        if not isinstance(term, str):
            continue
        t = " ".join(term.split())[:_MAX_TERM_CHARS].strip()
        key = t.lower()
        if t and key not in seen:
            seen.add(key)
            out.append(t)
        if len(out) >= _MAX_CONTEXT_TERMS:
            logger.warning("stt context truncated to %d terms",
                           _MAX_CONTEXT_TERMS)
            break
    return out


def _soniox_lang(language: str) -> str:
    """Soniox takes bare ISO codes ("hi"); the config carries Sarvam's regional
    form ("hi-IN"). Passing hi-IN through is not an error the API reports - it
    just synthesises something else."""
    return (language or "en").split("-")[0]


def _soniox_host(kind: str, region: str | None) -> str:
    """-> the host for one Soniox service in the region this key belongs to.

    `kind` is the subdomain the service already has: 'tts-rt', 'stt-rt', 'api'.
    None and 'us' both mean the unprefixed name, which is every key written
    before migration 058.

    The key and the host must agree - an India key on the US host is a 401 at
    connect, which reaches a call as a provider that will not start. The region
    comes from the same provider_keys row as the key: see
    store.load_provider_regions.

    Deliberately a copy of admin/backend/app/provider_keys.soniox_host rather
    than an import: the agent does not import the console's code, and three
    lines are cheaper than a shared package. If Soniox adds a region, both
    change - and the constant lists in that file are the place it will be
    noticed.
    """
    if not region or region == "us":
        return f"{kind}.soniox.com"
    return f"{kind}.{region}.soniox.com"


def _build_stt(provider: str, cfg, key: str, use_config_model: bool,
               region: str | None = None):
    """use_config_model is False for a fallback leg: cfg.stt_model names a model
    that belongs to the PRIMARY provider, and handing Sarvam's 'saarika:v2.5' to
    OpenAI fails at the first utterance rather than at startup.

    region is the provider region this key belongs to, and only Soniox has one."""
    if provider == "sarvam":
        kw = _stt_kwargs(cfg)
        if not use_config_model:
            kw["model"] = "saarika:v2.5"
        return sarvam.STT(**kw, api_key=key)
    if provider == "openai":
        model = (cfg.stt_model if use_config_model else None) or "gpt-4o-mini-transcribe"
        return openai.STT(model=model, api_key=key)
    if provider == "soniox":
        opts = {
            "model": (cfg.stt_model if use_config_model else None) or "stt-rt-v5",
            # The caller's language first, English second: these calls are
            # Hinglish, and the pair is what the hint field is for.
            "language_hints": [_soniox_lang(cfg.language), "en"],
            # Soniox defaults this to 2000 ms - its own endpointing would then
            # decide turns 500 ms after our local turn detector has already
            # given up. Tied to the same budget instead.
            "max_endpoint_delay_ms": int(MAX_ENDPOINTING * 1000),
        }
        # Only sent when the campaign has an opinion; otherwise the provider's
        # own defaults apply (level 0, sensitivity 0.0). Measured at those
        # defaults, Soniox averaged stt_ms 1067 against Sarvam's 238 - the
        # latency profile had simply never been chosen.
        #
        # Soniox's guidance: pick the level first, then fine-tune with
        # sensitivity, and never pair a high level with negative sensitivity.
        # The console says the same next to the fields.
        if use_config_model and cfg.stt_endpoint_level is not None:
            opts["endpoint_latency_adjustment_level"] = cfg.stt_endpoint_level
        if use_config_model and cfg.stt_endpoint_sensitivity is not None:
            opts["endpoint_sensitivity"] = float(cfg.stt_endpoint_sensitivity)

        # Terms the model has no reason to know. "Splendor Plus Flex" reached
        # the knowledge base as "Lender Plus Flex" and matched a different bike
        # at 0.57 - by then the wrong words were already in the transcript and
        # no prompt could undo it.
        #
        # Sent on the config leg only. A fallback leg runs because the first
        # provider is down, and that is not the moment to add an untested
        # parameter to the connection that is meant to rescue the call.
        terms = _context_terms(cfg) if use_config_model else []
        if terms:
            opts["context"] = soniox.ContextObject(terms=terms)
        return soniox.STT(
            api_key=key, params=soniox.STTOptions(**opts),
            base_url=f"wss://{_soniox_host('stt-rt', region)}/transcribe-websocket",
        )
    raise ValueError(f"unknown STT provider '{provider}'")


def _build_tts(provider: str, cfg, key: str, use_config_model: bool,
               region: str | None = None):
    if provider == "sarvam":
        kw = _tts_kwargs(cfg)
        if not use_config_model:
            kw["model"], kw["speaker"] = "bulbul:v3", "shubh"
        return sarvam.TTS(**kw, api_key=key)
    if provider == "openai":
        model = ((cfg.tts_model if use_config_model else None)
                 or tts_defaults.OPENAI_MODEL)
        return openai.TTS(model=model, api_key=key)
    if provider == "kokoro":
        # Ours, on the GPU box - and spoken to by our own client rather than
        # livekit's OpenAI plugin, which receives the audio and hands the
        # emitter zero bytes. The evidence is in kokoro_tts.py's docstring.
        #
        # No default URL, deliberately. A hardcoded address is what sent
        # production's calls to the development box for two days (REPLICA.md),
        # and a LAN address is exactly the kind that differs between servers.
        # Failing here names the variable; guessing would find the wrong box.
        if not KOKORO_URL:
            raise ValueError(
                "tts_provider is 'kokoro' but KOKORO_URL is not set on this "
                "server - it has no default, see migration 059")
        return kokoro_tts.TTS(
            base_url=KOKORO_URL,
            model=((cfg.tts_model if use_config_model else None)
                   or tts_defaults.KOKORO_MODEL),
            voice=((cfg.tts_voice if use_config_model else None)
                   or tts_defaults.KOKORO_VOICE),
        )
    if provider == "soniox":
        # tts_voice holds a Sarvam speaker name when Sarvam is primary, and a
        # Soniox one when Soniox is. The console validates that pairing; here we
        # only fall back to a default when it is empty.
        #
        # tts-rt-v2, NOT the plugin's own default of tts-rt-v1-preview. That is
        # an alias of tts-rt-v1, which Soniox deprecated with a removal date of
        # 31 Aug 2026 - a campaign left on it goes silent, mid-call, on a date
        # nothing in this repo would have warned about.
        #
        # The voice list differs between the two: v2 dropped Meera, Maya, Noah,
        # Jack, Claire, Sofia and Elise, and added Karan among many others. A
        # voice the model does not have fails at construction, so the console
        # reads the list from Soniox per model rather than holding its own copy.
        return soniox.TTS(
            api_key=key,
            model=((cfg.tts_model if use_config_model else None)
                   or tts_defaults.SONIOX_MODEL),
            language=_soniox_lang(cfg.language),
            voice=((cfg.tts_voice if use_config_model else None)
                   or tts_defaults.SONIOX_VOICE),
            sample_rate=_TTS_NATIVE_RATE["soniox"],
            # The region this campaign's key belongs to. On 23 Sep 2026 the
            # US region was stalling mid-sentence from here and the India one
            # was not - see migration 058 and gpu-server/BENCHMARKS.md.
            websocket_url=f"wss://{_soniox_host('tts-rt', region)}/tts-websocket",
        )
    raise ValueError(f"unknown TTS provider '{provider}'")


def _fallback_provider(layer: str, configured: str | None, primary: str,
                       keys: dict) -> str | None:
    """-> the fallback provider to use, or None with a reason logged.

    A fallback the client has no key for is not a fallback. Silently building it
    would produce a chain that reports itself as protected and fails on the
    first real outage - which is the one moment it exists for.
    """
    if not FALLBACK or not configured:
        return None
    if configured == primary:
        # The schema forbids this, so reaching here means the row predates the
        # constraint. Retrying the same dead provider twice is worse than no
        # fallback: the console would show one.
        logger.warning("%s fallback equals the primary (%s) - ignoring",
                       layer, primary)
        return None
    if configured not in KEYLESS and not keys.get(configured):
        logger.warning("%s fallback '%s' has no key for this campaign - "
                       "running on %s alone", layer, configured, primary)
        return None
    return configured


def _stt_stack(cfg, vad, keys: dict, regions: dict | None = None):
    regions = regions or {}
    primary = _build_stt(cfg.stt_provider, cfg, keys[cfg.stt_provider], True,
                         regions.get(cfg.stt_provider))
    fb = _fallback_provider("stt", cfg.stt_fallback_provider, cfg.stt_provider, keys)
    if not fb:
        return primary
    # vad is required: gpt-4o-mini-transcribe is not a streaming STT, so without
    # a VAD to chunk the audio it has nothing to send.
    return lk_stt.FallbackAdapter(
        [primary, _build_stt(fb, cfg, keys[fb], False, regions.get(fb))],
        vad=vad, attempt_timeout=ATTEMPT_TIMEOUT)


def _tts_stack(cfg, keys: dict, regions: dict | None = None):
    regions = regions or {}
    # .get, not [], since kokoro has no key to look up - see KEYLESS.
    primary = _build_tts(cfg.tts_provider, cfg, keys.get(cfg.tts_provider, ""),
                         True, regions.get(cfg.tts_provider))
    fb = _fallback_provider("tts", cfg.tts_fallback_provider, cfg.tts_provider, keys)
    if not fb:
        return primary
    # Note there is no attempt_timeout on the TTS adapter - unlike STT and LLM.
    return lk_tts.FallbackAdapter(
        [primary, _build_tts(fb, cfg, keys.get(fb, ""), False, regions.get(fb))],
        sample_rate=_TTS_NATIVE_RATE.get(cfg.tts_provider, 24000))


def _build_llm(provider: str, cfg, key: str, model: str):
    """One language model leg. The model is passed in, not read from cfg.

    Because the fallback runs a DIFFERENT model from the primary, and on a
    gateway the name is the routing: a campaign on gpt-4.1-mini falling back to
    OpenRouter wants "openai/gpt-4.1-mini" there, which is not the same string.
    """
    kw = {"model": model, "temperature": cfg.llm_temperature, "api_key": key}
    if provider in providers_mod.LLM_BASE_URL:
        kw["base_url"] = providers_mod.LLM_BASE_URL[provider]
    else:
        # prompt_cache_key is OpenAI's own parameter and means nothing to a
        # gateway. Sent anyway it is at best ignored and at worst a 400.
        #
        # Worth knowing what choosing a gateway costs: 90.8% of this system's
        # prompt tokens are served from OpenAI's cache, measured over 30 days,
        # and cached tokens are about a tenth of the price AND 393 ms faster
        # (1198 ms cold against 805 ms warm). A cheaper per-token rate
        # elsewhere has to beat all of that before it is actually cheaper.
        kw["prompt_cache_key"] = cfg.name
    return openai.LLM(**kw)


def _llm_stack(cfg, keys: dict):
    """The campaign's language model, and its own fallback behind it.

    The Gemini leg that used to sit here was hardcoded and ran on PLATFORM
    credentials - it authenticates with a service account file, and there is
    nowhere in the console to put one. So one leg of every campaign's language
    model was billed to us and could not be changed by anybody. It is gone.

    What replaces it is a choice: a campaign names its own fallback and pays for
    it on its own key, exactly as STT and TTS already do. A campaign that names
    none runs on one leg - which is a real reduction in resilience and the
    reason this is a visible setting rather than a silent default.
    """
    provider = cfg.llm_provider or "openai"
    primary = _build_llm(provider, cfg, keys[provider], cfg.llm_model)
    if not FALLBACK:
        return primary

    # getattr, like prompt_datetime and kb_filler_enabled above, and for the
    # reason this file learned the hard way: a column added to agent_config and
    # not added to the AgentConfig dataclass in store.py reaches here as an
    # AttributeError, and an AttributeError in here kills the call at 0s.
    #
    # A MISSING FALLBACK FIELD SHOULD MEAN NO FALLBACK, which is exactly what
    # None gives - the documented degradation rather than a dead call. Matching
    # the failure to what the field means is the whole point of the default.
    fb = _fallback_provider("llm", getattr(cfg, "llm_fallback_provider", None),
                            provider, keys)
    # No model, no fallback. Unlike STT and TTS there is no provider default to
    # reach for, so a half-configured fallback would fail at the moment it was
    # needed rather than at the moment it was saved.
    fb_model = (getattr(cfg, "llm_fallback_model", None) or "").strip()
    if not fb or not fb_model:
        if fb:
            logger.warning("llm fallback '%s' has no model set - "
                           "running on %s alone", fb, provider)
        return primary

    return lk_llm.FallbackAdapter(
        [primary, _build_llm(fb, cfg, keys[fb], fb_model)],
        attempt_timeout=ATTEMPT_TIMEOUT)


async def _end_room(room_name: str) -> None:
    """Force the SIP leg down for a call this agent will not serve.

    Simply returning is not enough. livekit-sip does not answer until an agent
    subscribes, so the caller would just hear ringing until Asterisk's 25 s Dial
    timeout. Deleting the room ends it immediately, and Asterisk falls straight
    through to the human extension.
    """
    lkapi = api.LiveKitAPI(url=_api_url(),
                           api_key=os.environ["LIVEKIT_API_KEY"],
                           api_secret=os.environ["LIVEKIT_API_SECRET"])
    try:
        await lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))
    except Exception:
        logger.exception("could not end room %s - the caller will ring out", room_name)
    finally:
        await lkapi.aclose()


# Where each provider's plugin sends its requests. Used only to open the
# connection early - see _warm_providers.
#
# Region-blind on purpose, and it has to be: this runs at process start, before
# any job has a campaign, and the region belongs to a campaign's key. What it
# costs a call on another region is one connection setup on the first request -
# the same as before any of this existed.
_PROVIDER_HOSTS = {
    "sarvam": "https://api.sarvam.ai",
    "openai": "https://api.openai.com",
    "soniox": "https://api.soniox.com",
}


# Where a REFER is addressed. The host does not decide anything - livekit sends
# the REFER on the existing dialog, so it reaches our Asterisk whatever is
# written here - but it has to be a real URI and it should say something true.
TRANSFER_SIP_HOST = os.getenv("TRANSFER_SIP_HOST", "10.130.9.243")


# ─────────────────────── what actually went wrong ───────────────────────

# The plugin label names the provider: 'livekit.plugins.openai.llm.LLM'. A
# FallbackAdapter names itself instead, which is why provider comes out NULL
# for those - it genuinely does not say which leg failed.
_PROVIDER_RE = re.compile(r"livekit\.plugins\.(\w+)\.")
_SOURCE_BY_TYPE = {"LLMError": "llm", "STTError": "stt", "TTSError": "tts"}


def _error_details(ev) -> dict:
    """Break a session error into columns that can be counted.

    calls.outcome keeps the whole sentence. This is the part an alert can act
    on: which leg, whose service, and what the status code was. 429 is a rate
    limit, 402 is out of credit and 401 is a bad key - three different problems
    for three different people, and until now all three read as "an error".
    """
    err = getattr(ev, "error", ev)
    detail = {
        "source": _SOURCE_BY_TYPE.get(type(ev).__name__, "other"),
        "provider": None,
        "code": None,
        "message": str(err)[:400],
    }

    label = getattr(ev, "label", "") or ""
    m = _PROVIDER_RE.search(label)
    if m and m.group(1) != "fallback_adapter":
        detail["provider"] = m.group(1)

    # Walk the cause chain before giving up on the code. A FallbackAdapter
    # reports "all LLMs failed (...)" and the 429 underneath it is what anyone
    # reading the alert actually needs; if Python chained it, it is here.
    seen = 0
    cause = err
    while cause is not None and seen < 5:
        code = getattr(cause, "status_code", None)
        if isinstance(code, int) and code > 0:
            detail["code"] = code
            if detail["provider"] is None:
                m = _PROVIDER_RE.search(str(getattr(cause, "label", "")))
                if m:
                    detail["provider"] = m.group(1)
            break
        cause = getattr(cause, "__cause__", None)
        seen += 1

    return detail


def _transfer_target(cfg) -> str:
    """The SIP URI a handoff is sent to.

    With a dialler chosen, what travels is the CAMPAIGN and not the extension:
    two campaigns can use 5000 on different diallers, so the extension
    identifies nothing on its own. Asterisk looks the pair up at REFER time
    against a view that holds three columns and nothing else.

    Without one, transfer_to is used exactly as before. A campaign moves over
    when somebody gives it a dialler, and not before.
    """
    if getattr(cfg, "transfer_dialler_id", None) and cfg.campaign_id:
        return f"sip:c{cfg.campaign_id}@{TRANSFER_SIP_HOST}"
    return cfg.transfer_to


async def _warm_tts(tts, seen: set[str]) -> None:
    """Open the TTS connection while the cached greeting is playing.

    The greeting used to do this by accident: it was the first synthesis in the
    process, so it paid for the connection and every later turn was cheap -
    1458-1519 ms for the greeting against 619-701 ms afterwards.

    Caching the greeting removed that, and the bill did not disappear. It moved
    onto the caller's first question, where on call 339 it cost 6286 ms. The
    caller asked, heard nothing, said "हेलो" - and that word interrupted the
    answer just as it began. They hung up sixteen seconds later. As the
    greeting it was slow; as the reply it ended the call.

    So it is paid here instead, inside the 7.2 s of greeting the caller is
    already listening to. Six characters, and even a 6 s cold start finishes
    before they have drawn breath.

    Failures are ignored on purpose: this is an optimisation, and a TTS that
    cannot be reached here will fail properly and visibly at the first reply.

    `seen` collects this synthesis's request ids so the metrics handler can tell
    them apart from the call's own. Without that the warm-up's 1.9 s lands on
    the greeting's row, and the console reports a greeting that took 1640 ms
    when it in fact took none at all - a number somebody would eventually spend
    an afternoon chasing.
    """
    async def once(text: str) -> float:
        t = time.perf_counter()
        async for ev in tts.synthesize(text):
            # Claimed before the stream ends, and the metrics arrive when it
            # ends - so by the time anybody asks, this id is already spoken for.
            seen.add(ev.request_id)
        return (time.perf_counter() - t) * 1000

    try:
        cold = await once("नमस्ते")
        # A SECOND one, and it is the more useful number.
        #
        # Every turn of every call has sat at 700-730 ms of tts_ttfb, and that
        # flatness says fixed overhead rather than synthesis. What it does not
        # say is whose: the plugin buffers the model's output into complete
        # SENTENCES before sending any of it, so a turn's tts_ttfb includes
        # waiting on the LLM to finish a sentence - it is partly a measure of
        # the language model, wearing the TTS's name.
        #
        # This call cannot be: a whole string goes in at once, so the tokenizer
        # has nothing to wait for and the connection is already open. Warm
        # websocket plus Soniox, and nothing else.
        #
        #   ~700 ms here -> the floor is Soniox's, and only a provider change
        #                   moves it
        #   ~250 ms here -> the rest is the tokenizer waiting on the LLM, and
        #                   that is worth attacking
        warm = await once("ठीक है")
    except Exception:
        logger.debug("tts warm-up failed - the first reply will pay for it",
                     exc_info=True)
        return
    logger.info("TIMING tts_warm=%dms  tts_warm2=%dms", cold, warm)


async def _warm_providers() -> None:
    """DNS, TCP and TLS to the provider hosts, before anything needs them.

    The first thing a caller hears is a synthesised greeting, and it is
    measurably slower than every utterance after it: 774 ms on average against
    ~270 ms for later turns, and up to 2.5 s. The difference is the cost of
    opening the connection, paid once per job process - and a job process
    handles exactly one call, so every caller pays it.

    This runs concurrently with ctx.connect() and the config load. That used to
    be free - those took well over a second - but fixing the kb import brought
    startup down to ~390ms while this still takes ~820ms. It now finishes after
    the greeting has already been asked for, which is what `warm_done=False` in
    the timing log below has been reporting on every call.

    It still earns its place on the LLM leg, which nothing needs until the
    caller has finished their first sentence. It does nothing for the greeting,
    and the greeting is why it was written - see greeting_cache, which fixes
    that end properly by not needing the provider at all.

    All three hosts, not just the campaign's own - the campaign is not known
    yet, and two extra TLS handshakes on a LAN cost nothing next to what they
    might save. Failures are ignored: this is an optimisation, and a provider
    that is unreachable here will report itself properly at the first real
    request.

    Whether the plugins reuse this connection depends on their using the shared
    per-job http session, which is why the timing log below reports the greeting
    latency either way rather than assuming an improvement.
    """
    try:
        from livekit.agents.utils import http_context

        session = http_context.http_session()
    except Exception as e:
        logger.debug("provider warm-up unavailable: %s", e)
        return

    async def one(host: str) -> None:
        try:
            # HEAD on the root: no auth, no body, and the response status is
            # irrelevant. All that matters is that the socket is now open.
            async with session.head(host, timeout=aiohttp.ClientTimeout(total=3)):
                pass
        except Exception:
            pass

    t = time.perf_counter()
    await asyncio.gather(*(one(h) for h in _PROVIDER_HOSTS.values()))
    # Worth logging because the first version of this never finished in time:
    # `import kb` was still being done lazily inside the job, and a synchronous
    # import blocks the event loop, so this task got no chance to run at all.
    logger.info("TIMING provider_warm=%dms", int((time.perf_counter() - t) * 1000))


async def _queue_postback(store, cfg, call_id: int, keys: dict,
                          dialler: dict) -> None:
    """Extract what the conversation established and queue it for the client.

    Everything here is best effort and nothing raises. A call that has already
    happened must not be damaged by trouble sending a report about it.
    """
    if not getattr(cfg, "postback_enabled", False):
        return
    # Everything this function does happens inside the shutdown budget, and
    # until call 590 nobody knew how much of it was being used. Logged so the
    # question "is 45 seconds enough" has an answer in the journal rather than
    # in somebody's judgement. It covers the DB reads as well as the
    # extraction - the extraction logs its own share separately.
    t0 = time.monotonic()
    try:
        import postback as pb

        turns = await store.load_turns(call_id)
        # Values the caller was never read aloud - a dealer code, an id - live
        # only in what the tool answered. Empty unless a tool has keep_response
        # set, so this changes nothing for a campaign that has not asked for it.
        tool_calls = await store.load_tool_calls(call_id)
        row = await (await store.pool()).fetchrow(
            """SELECT id, started_at, ended_at, duration_ms, caller, callee,
                      end_reason, outcome, transferred_to, turn_count,
                      sip_call_id
                 FROM calls WHERE id = $1""", call_id)

        fields = cfg.postback_fields or []
        if isinstance(fields, str):
            # JSONB as text again - see store.load_tools. Guarded rather than
            # trusted, because this one is read once per call at shutdown and a
            # silent empty list would look exactly like "nothing configured".
            import json as _json
            try:
                fields = _json.loads(fields)
            except Exception:
                fields = []

        # The campaign's OWN language model, not openai by assumption.
        #
        # This read keys["openai"] and sent cfg.llm_model to it. On a campaign
        # using a gateway both halves are wrong at once: there may be no OpenAI
        # key at all, and "google/gemma-4-26b-a4b-it" is a name OpenAI has never
        # heard of. It failed inside the except below, so no postback row was
        # written and the console showed nothing missing - the call simply never
        # reached the customer's system.
        llm_provider = getattr(cfg, "llm_provider", None) or "openai"
        extracted = await pb.extract(
            turns=turns, fields=fields,
            api_key=keys.get(llm_provider, ""),
            base_url=providers_mod.llm_base_url(llm_provider),
            tool_calls=tool_calls, model=cfg.llm_model)

        # Fields the dialler already told us, republished under the names the
        # client's endpoint asks for. AFTER extract and merged over the top: if a
        # key were somehow configured as both, the dialler's value is a fact and
        # the model's reading of it is an opinion.
        #
        # This is what makes them reach a client running the flat payload, where
        # only `extracted` is sent and the `dialer` block is not. Those values sat
        # in the database, correct and unsent, for every such campaign.
        extracted.update(pb.from_dialler(fields, dialler))

        payload = pb.envelope(
            call_row=dict(row) if row else {"id": call_id},
            dialler=dialler,
            extracted=extracted,
            turns=turns if cfg.postback_include_transcript else None,
            tool_calls=tool_calls,
            full=getattr(cfg, "postback_full_payload", True))

        await store.save_postback(call_id, cfg.campaign_id, payload)
        logger.info("postback queued for call %s (%d fields) in %dms",
                    call_id, len(extracted), (time.monotonic() - t0) * 1000)
    except Exception:
        logger.exception("postback could not be prepared for call %s "
                         "after %dms", call_id, (time.monotonic() - t0) * 1000)


async def entrypoint(ctx: JobContext):
    import store

    # Every phase below is timed against this. Four separate debugging sessions
    # have now had to reconstruct where a call's first two seconds go from
    # adjacent log timestamps, and each time the answer was somewhere nobody had
    # guessed - the six database round trips people assumed were the problem
    # take 8 ms between them.
    t0 = time.monotonic()

    def since() -> int:
        return int((time.monotonic() - t0) * 1000)

    # Started before connect and never awaited: it must overlap the wait, not
    # add to it. Safe to leave running - it swallows every exception and is
    # bounded by its own 3 s timeout, so it cannot outlive the call meaningfully
    # or surface as an unretrieved task exception.
    warm = asyncio.create_task(_warm_providers())

    # Connect first: which campaign this call belongs to is decided by the
    # number that was dialled, and that only arrives with the SIP participant.
    # livekit-sip creates the participant before dispatching this job, so it is
    # already there.
    await ctx.connect()
    logger.info("TIMING connect=%dms", since())

    caller = callee = sip_call_id = None
    sip_participant = None
    for p in ctx.room.remote_participants.values():
        if _sip_attr(p, "sip.callIDFull") or p.identity.startswith("sip_"):
            sip_participant = p
        caller = caller or _sip_attr(p, "sip.phoneNumber", "sip.from_user")
        callee = callee or _sip_attr(p, "sip.trunkPhoneNumber", "sip.to_user")
        # sip.callIDFull, NOT sip.callID. The latter is LiveKit's own identifier
        # (SCL_7c3USwsGRuui); only callIDFull carries the SIP Call-ID header that
        # Asterisk names the recording after. No fallback between them on
        # purpose - storing the wrong one would silently produce a column that
        # never matches a file while looking perfectly populated.
        sip_call_id = sip_call_id or _sip_attr(p, "sip.callIDFull")

    cfg = None
    if callee:
        # A dialled number that is not routed is REFUSED, not served by a default
        # agent. Falling back would mean one client's caller reaching another
        # client's agent, and it would make the routing list decorative - every
        # number the PBX forwards would answer whether configured or not.
        try:
            cfg = await store.load_config_for_did(callee)
        except store.CampaignUnavailable as e:
            logger.warning("DECLINED call to %s: %s", callee, e)
            await _end_room(ctx.room.name)
            return
        if cfg is None:
            logger.warning("DECLINED call to %s: no campaign routes this number",
                           callee)
            await _end_room(ctx.room.name)
            return
    else:
        # No dialled number at all - a manual `dev` run or a non-SIP job. There
        # is nothing to route on, so the env config is the only sensible answer.
        cfg = await store.load_config(CONFIG_NAME)
        logger.info("no dialled number on this job - using AGENT_CONFIG=%s",
                    CONFIG_NAME)

    # Whose provider account this call runs on: the campaign's own keys, or the
    # client's. A missing key ends the call here rather than at the first
    # utterance - the caller falls through to a human, which is the same
    # treatment a disabled campaign gets, and for the same reason.
    keys: dict[str, str] = {}
    # Which region each of those keys belongs to. Read beside the keys and from
    # the same rows, because a key and a host that disagree are a 401 at connect
    # and a call that never speaks - see store.load_provider_regions.
    regions: dict[str, str | None] = {}
    if cfg.campaign_id is not None:
        keys = await store.load_provider_keys(cfg.campaign_id)
        regions = await store.load_provider_regions(cfg.campaign_id)
        # Whichever providers THIS campaign actually uses - not a fixed set.
        #
        # openai used to be unconditional here because the LLM ran on it. Since
        # 049 the LLM names its own provider, so that assumption had to go - a
        # campaign on OpenRouter would otherwise have been declined for want of
        # a key it does not use.
        #
        # But openai comes back the moment the knowledge base is on: retrieval
        # embeds the query through text-embedding-3-small whatever the language
        # model is. Getting that wrong would let the call start and then fail
        # every search, which is the quieter and worse failure.
        needed = {cfg.stt_provider, cfg.tts_provider, cfg.llm_provider or "openai"}
        if cfg.kb_enabled:
            needed.add("openai")
        # A provider we host ourselves has no account and no key. Leaving it in
        # here would decline every call on a campaign whose voice is our own
        # box, for want of a credential that cannot exist.
        needed -= set(KEYLESS)
        missing = sorted(p for p in needed if not keys.get(p))
        if missing:
            logger.warning("DECLINED call to %s: campaign %s has no %s key",
                           callee, cfg.campaign_id, " or ".join(missing))
            await _end_room(ctx.room.name)
            return
    else:
        # A `dev` run against AGENT_CONFIG has no campaign and therefore no
        # client to bill. Platform keys are the only thing available, and this
        # path never serves a real caller.
        # Whatever the environment happens to hold. Built by lookup rather than
        # as a fixed pair so a dev run against a soniox config does not die with
        # a KeyError three lines later.
        keys = {p: os.environ[v] for p, v in
                (("openai", "OPENAI_API_KEY"),
                 ("sarvam", "SARVAM_API_KEY"),
                 ("soniox", "SONIOX_API_KEY")) if os.environ.get(v)}
        logger.info("no campaign on this job - using the platform keys (%s)",
                    ",".join(sorted(keys)) or "none")

    # built in prompt.py so the cache warmer emits a byte-identical prefix
    instructions, kb_mode, kb_tokens = await prompt_mod.build_instructions(cfg)

    # Appended AFTER that call, never inside it. The warmer runs the same
    # function and must produce a byte-identical string; a clock in there would
    # differ by a second and quietly create a second cache entry, which is the
    # exact failure the module docstring warns about.
    #
    # Out here it stays safe, because what the warmer produces remains an exact
    # PREFIX of this. Everything above - the whole knowledge base index, every
    # rule - still caches. Only the last few tokens are new, and only once per
    # call: a clock that ticked every turn would make every turn a cache miss,
    # and a three-minute call is not worth 97% of the prompt.
    if getattr(cfg, "prompt_datetime", False):
        instructions += "\n\n" + prompt_mod.now_line(getattr(cfg, "prompt_timezone", None))

    logger.info("config=%s lang=%s llm=%s kb=%s(%s, %d tok) transfer=%s->%s",
                cfg.name, cfg.language, cfg.llm_model, cfg.kb_enabled, kb_mode,
                kb_tokens, cfg.transfer_enabled, _transfer_target(cfg))
    logger.info("TIMING config+keys+prompt=%dms  preemptive_tts=%s",
                since(), PREEMPTIVE_TTS)

    call_id = await store.start_call(ctx.room.name, caller, callee, cfg.name,
                                     cfg.language, cfg.campaign_id, sip_call_id)
    logger.info("call_id=%s caller=%s callee=%s", call_id, caller, callee)

    # Registered HERE, before anything that can raise. Everything below - the
    # TTS constructor, the LLM constructor, the prompt build - can throw on a
    # bad config, and until this exists the row created above would never be
    # closed by anything. Six of them sat in the live monitor as "stuck calls"
    # after a bad voice took the workers down.
    #
    # It only writes when nothing else has, so the real shutdown handler still
    # sets the accurate reason whichever order they run in.
    async def _safety_net():
        # The constant, not the words. end_call_usage matches on this exact
        # string to clear it when the call turns out to have been fine, and two
        # copies would mean a healthy call wearing a failure message forever.
        await store.end_call_if_open(call_id, "error", store.SAFETY_NET_OUTCOME)

    ctx.add_shutdown_callback(_safety_net)

    # Read LAST, not with the other attributes above. headers_to_attributes is
    # populated asynchronously by livekit-sip, and everything between the two
    # points - the config load, the prompt build, start_call - has given it time
    # to land. Logged either way, so a campaign that should have context and does
    # not is visible rather than merely quieter.
    dialler = _dialler_attrs(sip_participant)
    caller_ctx, given = _caller_context(dialler, instructions)
    if dialler:
        logger.info("dialler context: %s",
                    " ".join(f"{k.split('.', 1)[1]}={v}" for k, v in dialler.items()))
        logger.info("given to the model: %s",
                    ", ".join(k.split(".", 1)[1] for k in given)
                    or "nothing - the prompt uses no dialler placeholder")
        await store.set_dialler_context(call_id, dialler, given)
    else:
        logger.info("dialler context: none on this call")

    # Tools the campaign defined in the console, on top of the two built in
    # here. Built AFTER start_call so every invocation can be recorded against a
    # call id - writes are in scope, and "which call booked this?" has to be
    # answerable.
    # Tools are built before the session exists, so they cannot hold a
    # reference to it. This box is filled in a few lines below, and a tool that
    # wants to say something reads it then - by which time there is always a
    # session, because a tool can only run during a call.
    live: dict = {}

    async def _tool_says(line: str) -> None:
        session = live.get("session")
        if session is None:
            logger.warning("a tool wanted to speak before the session existed")
            return
        h = session.say(line, allow_interruptions=True)
        agent.filler_ids[h.id] = line
        await h

    def _record_tool(**kw):
        # The tool's own timing, placed on the turn's timeline as well, so a
        # slow lookup shows up as the reason for a slow answer.
        ms = kw.get("duration_ms")
        if ms is not None:
            now = time.time()
            agent.heard.append({"kind": "lookup", "label": kw.get("name") or "tool",
                                "start": now - ms / 1000, "end": now})
        return store.record_tool_call(call_id, **kw)

    extra_tools = []
    if cfg.campaign_id is not None:
        specs = await store.load_tools(cfg.campaign_id)
        if specs:
            extra_tools = tools_mod.build_all(
                specs, call_id,
                _record_tool,
                _tool_says,
                functools.partial(store.record_gap, call_id, cfg.campaign_id),
                filler_after_s=_lookup_filler_s(cfg))
            logger.info("campaign tools: %s",
                        ", ".join(s["name"] for s in specs))

    agent = KBAgent(instructions, cfg, kb_mode, ctx.room, keys,
                    chat_ctx=caller_ctx,
                    extra_tools=extra_tools)
    agent.call_id = call_id

    vad = ctx.proc.userdata["vad"]
    # Held rather than passed inline: the greeting cache renders through this
    # same stack, so a campaign's fallback provider applies there too.
    tts_stack = _tts_stack(cfg, keys, regions)
    session = AgentSession(
        stt=_stt_stack(cfg, vad, keys, regions),
        llm=_llm_stack(cfg, keys),
        tts=tts_stack,
        vad=vad,
        turn_detection=MultilingualModel(),
        allow_interruptions=cfg.allow_interrupt,
        # turn_handling rather than min_endpointing_delay/max_endpointing_delay,
        # which this version marks deprecated. The values are unchanged - 0.25
        # and 1.5, both earned: 4.0 froze calls for four seconds when a short
        # closing scored below the detector's threshold.
        #
        # Worth moving for its own sake. The new API's own defaults are 0.5 and
        # 3.0, so the day the deprecated path is removed, an untouched agent
        # would quietly go back to the behaviour that was fixed.
        turn_handling={
            "endpointing": {"min_delay": MIN_ENDPOINTING,
                            "max_delay": MAX_ENDPOINTING},
            # enabled is left alone: it defaults to True and already runs the
            # LLM ahead of the turn. Only the TTS half is being switched here.
            "preemptive_generation": {"preemptive_tts": PREEMPTIVE_TTS},
        },
    )

    live["session"] = session
    agent.session_ref = session

    usage = metrics.UsageCollector()
    pending: dict[str, int] = {}
    # TTS requests this call made for its own sake rather than for the caller.
    # Their timings belong to nobody's turn.
    warm_requests: set[str] = set()
    seq = 0

    # ---- guardrails ----
    # agent_config has carried max_turns and max_duration_sec since Step 8 but
    # nothing ever read them: a call could run forever and a looping LLM had no
    # brake at all. One observed call used 32,816 prompt tokens, so the token cap
    # is the one that actually bounds spend - the KB rides along on every request.
    async def enforce_limit(reason: str):
        if agent.limit_hit:
            return
        agent.limit_hit = reason
        logger.warning("LIMIT HIT (%s) - closing call %s", reason, call_id)
        try:
            msg = cfg.limit_message or "Is call ka samay poora ho gaya hai. Dhanyavaad."
            h = await session.say(msg, allow_interruptions=False)
            await h.wait_for_playout()      # never cut the caller off mid-sentence
        except Exception:
            logger.exception("limit message failed - closing anyway")
        try:
            await ctx.delete_room()
        except Exception:
            logger.exception("delete_room failed")
            ctx.shutdown(reason=f"limit:{reason}")

    async def duration_watchdog():
        deadline = time.monotonic() + cfg.max_duration_sec
        while not agent.limit_hit:
            await asyncio.sleep(5)
            if time.monotonic() >= deadline:
                await enforce_limit(f"max_duration_sec={cfg.max_duration_sec}")
                return

    asyncio.create_task(duration_watchdog())

    # ---- silence, and ending the call on purpose ----
    # Both live in one loop because they are the same question asked once a
    # second: is the agent finished talking, and if so, should this call still
    # be open?
    #
    # Driven by timestamps rather than by awaiting an event, so a missed or
    # renamed event degrades into "the timer never fires" instead of a task
    # wedged forever on something that will not arrive.
    last_activity = time.monotonic()
    # True unless the agent is sitting waiting for the caller. Not "is it
    # speaking": see the handler below for why that was not enough.
    agent_busy = False
    silence_attempts = 0
    # Set the moment the session ends, whatever ended it. The watchdog's other
    # exit conditions - a limit, a transfer - do not cover the ordinary case of
    # somebody hanging up.
    closed = False

    # One write, from whichever of the two below comes first. See
    # store.mark_ended for why the row cannot wait for the shutdown handler.
    end_stamp: asyncio.Task | None = None

    def _stamp_end() -> None:
        nonlocal end_stamp
        if end_stamp is None:
            end_stamp = asyncio.create_task(
                store.mark_ended(call_id, agent.turn_count))

    @ctx.room.on("participant_disconnected")
    def _on_participant_gone(p):
        # The caller's hangup, straight from the room. This is the one proven
        # to fire on call 464 - livekit's own "closing agent session due to
        # participant disconnect" is its reaction to it.
        #
        # Not the session's close event alone: the session awaits any
        # uninterruptible speech before it emits that, and the greeting is
        # uninterruptible. A caller who hangs up during it is exactly the call
        # where "close" comes late or not at all.
        if sip_participant is None or p.identity == sip_participant.identity:
            _stamp_end()

    # ---- speech trace ----
    # Call 618 (22 Sep 2026): from 09:30:00 nothing the agent tried to say was
    # heard - not the answer after a search, not the silence prompt - and the
    # log could not say why, because livekit reports none of it at INFO. One
    # speech that never finishes holds every speech behind it; these lines
    # name it. Every speech created and finished, every false interruption,
    # and whether the audio output was paused at the time.
    speech_born: dict[str, float] = {}
    filler_playing: dict[str, dict] = {}

    @session.on("speech_created")
    def _on_speech_created(ev):
        h = ev.speech_handle
        speech_born[h.id] = time.monotonic()
        logger.info("SPEECH + %s source=%s interruptible=%s output=%s",
                    h.id, ev.source, h.allow_interruptions,
                    _audio_output_state(session))

        def _done(handle):
            ev = filler_playing.pop(handle.id, None)
            if ev is not None:
                ev["end"] = time.time()
            born = speech_born.pop(handle.id, None)
            logger.info("SPEECH - %s interrupted=%s after %sms", handle.id,
                        handle.interrupted,
                        int((time.monotonic() - born) * 1000) if born else "?")

        h.add_done_callback(_done)

    @session.on("agent_false_interruption")
    def _on_false_interruption(ev):
        logger.info("FALSE_INTERRUPTION resumed=%s output=%s",
                    getattr(ev, "resumed", None), _audio_output_state(session))

    @session.on("user_state_changed")
    def _on_user_state(ev):
        logger.info("USER %s -> %s", getattr(ev, "old_state", None),
                    getattr(ev, "new_state", None))
        if getattr(ev, "new_state", None) == "listening":
            agent.user_quiet_at = time.time()

    @session.on("close")
    def _on_close(_ev=None):
        nonlocal closed
        closed = True
        # For the calls the AGENT ends - a limit, a goodbye, a transfer. Those
        # can close the session without the caller's disconnect arriving first.
        _stamp_end()

    @session.on("agent_state_changed")
    def _on_agent_state(ev):
        nonlocal agent_busy, last_activity
        state = getattr(ev, "new_state", None)
        logger.info("AGENT %s -> %s  output=%s", getattr(ev, "old_state", None),
                    state, _audio_output_state(session))
        if state == "speaking":
            # "speaking" is set on a speech's first audio frame, so this is the
            # moment the caller started hearing it - not when it was queued.
            cur = getattr(session, "current_speech", None)
            said = agent.filler_ids.pop(cur.id, None) if cur is not None else None
            if said is not None:
                ev_ = {"kind": "filler", "label": said, "start": time.time(), "end": None}
                agent.heard.append(ev_)
                filler_playing[cur.id] = ev_
        # Anything that is not "listening" means the agent has the floor -
        # thinking counts as much as speaking.
        #
        # It used to be `state == "speaking"`, and that let the prompt fire on
        # top of the agent's own voice. When a model answers AND calls a tool in
        # one turn, the preamble is still playing out when the state moves to
        # "thinking" for the tool - so the flag went false mid-sentence, the
        # clock had been running since the caller last spoke, and the caller
        # heard "क्या आप मुझे सुन पा रहे हो?" over the top of the answer they
        # had asked for.
        #
        # Three times on call 368, every one of them on a turn that spoke and
        # called a tool together:
        #
        #   15:51:02  [assistant] ...ईएमआई की गणना करता हूँ
        #   15:51:02  silence 10s - prompt 1/2
        #
        # Same second. And correct by the old rule, which is what made it worth
        # writing down.
        agent_busy = state != "listening"
        if state == "listening":
            # The clock starts when the AGENT stops, not when the caller last
            # spoke. Otherwise a long answer from the agent counts as the
            # caller's silence and the prompt fires the moment it finishes.
            last_activity = time.monotonic()

    @session.on("user_input_transcribed")
    def _on_user_input(ev):
        nonlocal last_activity, silence_attempts
        if not (getattr(ev, "transcript", "") or "").strip():
            return
        # Speech of any kind, finished or not, proves somebody is still there.
        last_activity = time.monotonic()
        # Any real speech clears the count. Two unanswered prompts an hour
        # apart are not a caller who has gone away.
        silence_attempts = 0
        # This event fires for interim transcripts as well - is_final is right
        # there on it - and the counter was taking every one of them. So a turn
        # was counted several times over, on fragments of a word rather than on
        # an answer.
        #
        # That matters here because the transfer confirmation gate waits for the
        # caller to say yes, and it waits by watching this number. Counting the
        # first syllable of a half-heard reply as consent is not what it is for.
        if not getattr(ev, "is_final", True):
            return
        # The only counter that gate trusts. Incremented here and nowhere else,
        # so nothing the agent does can advance it.
        agent.user_turns += 1

    async def _hangup(reason: str | None) -> None:
        if reason:
            agent.ended_by = reason
        try:
            await ctx.delete_room()
        except Exception:
            logger.exception("delete_room failed - falling back to shutdown")
            ctx.shutdown(reason=reason or "ended")

    async def call_watchdog():
        prompts = list(cfg.silence_prompts or [])
        timeout = cfg.silence_timeout_sec
        nonlocal silence_attempts, last_activity

        # `closed` is what actually stops this. The loop used to run on until the
        # limit or a transfer, neither of which happens when the caller simply
        # hangs up - so after a call ended the watchdog carried on noticing
        # silence and tried to speak into a session that was gone:
        #
        #   RuntimeError: AgentSession isn't running
        #
        # Twice, on a real call. Nobody heard it, which is the only reason it
        # went unnoticed.
        while not agent.limit_hit and agent.transferred is None and not closed:
            await asyncio.sleep(1)
            if agent_busy:
                continue

            # A marker was seen. Acted on HERE and not in tts_node, because that
            # runs while audio is still being produced - transferring or hanging
            # up there would cut off the sentence the marker was attached to.
            # Transfer wins over end-of-call, and clears it.
            #
            # A model asked to hand over will cheerfully write BOTH markers in
            # one exchange - observed live: "connecting you [TRANSFER]" and then
            # "thank you, have a good day [EOC]". They are contradictory
            # instructions, and honouring both made the caller hear a farewell
            # and then a hold message. Handing the call to a person is the one
            # that must survive: hanging up on someone who asked for a human is
            # the worse failure by a distance.
            if agent.transfer_requested:
                agent.transfer_requested = False
                agent.end_requested = False
                # Same path as the tool, so the confirmation gate applies to
                # both. With confirmation on, the first marker only asks; the
                # caller's reply and a second marker are what transfer.
                await agent.request_transfer(session, "end-of-turn marker")
                continue

            # Once a handoff is under way the call belongs to the other end.
            if agent.transferred is not None:
                return

            if agent.end_requested:
                # A handoff has been asked for and not finished. Observed live:
                # the confirmation gate correctly refused a transfer because the
                # caller had not answered yet, and the [EOC] in the very same
                # response then hung up on them one second later - so a caller
                # who asked for a person got a dial tone instead.
                #
                # Nothing the model writes should be able to end a call that is
                # mid-handoff. Cleared rather than remembered: if the caller
                # declines the transfer and the conversation genuinely finishes
                # later, a fresh marker should still work.
                if agent.transfer_asked_at is not None and agent.transferred is None:
                    logger.info("end-of-call marker ignored - a handoff is "
                                "still pending on call %s", call_id)
                    agent.end_requested = False
                    continue
                logger.info("end-of-call marker seen - closing call %s", call_id)
                await _hangup(None)     # a finished conversation is "completed"
                return

            if not timeout or not prompts:
                continue
            if time.monotonic() - last_activity < timeout:
                continue

            line = prompts[min(silence_attempts, len(prompts) - 1)]
            silence_attempts += 1
            final = silence_attempts >= len(prompts)
            cur = getattr(session, "current_speech", None)
            logger.info("silence %ds - prompt %d/%d%s  current_speech=%s output=%s",
                        timeout, silence_attempts, len(prompts),
                        " (last)" if final else "",
                        cur.id if cur is not None else None,
                        _audio_output_state(session))
            try:
                handle = await session.say(line, allow_interruptions=not final)
                await handle.wait_for_playout()
            except RuntimeError as e:
                # The session went away between the check above and here - the
                # caller hung up mid-prompt. Not a fault, and not worth a
                # traceback: there is nobody left to say anything to.
                if "isn't running" in str(e) or "not running" in str(e):
                    logger.info("silence prompt skipped - the call had ended")
                    return
                logger.exception("silence prompt failed")
            except Exception:
                logger.exception("silence prompt failed")

            if final:
                await _hangup("no_response")
                return
            # Restart the clock from the end of OUR prompt, not from when the
            # caller last spoke - otherwise every remaining attempt fires at
            # once, one second apart.
            last_activity = time.monotonic()

    asyncio.create_task(call_watchdog())

    # Which provider actually served this call, as opposed to which one the
    # config asked for. A set, because a call can start on the primary and fall
    # back partway - "sarvam" means the fallback never fired, "sarvam,openai"
    # means it did. Without this the only evidence a fallback ever ran is a
    # resampling line in the worker journal.
    providers_used: dict[str, set[str]] = {"stt": set(), "llm": set(), "tts": set()}

    def _note_provider(layer: str, label: str | None):
        if not label:
            return
        # "livekit.plugins.sarvam.tts.TTS" -> "sarvam". Anything that does not
        # match keeps its raw label rather than being dropped: an unrecognised
        # provider is exactly the case worth seeing.
        parts = label.split(".")
        name = parts[2] if len(parts) > 3 and parts[1] == "plugins" else label[:40]
        # A gateway rides the openai plugin, so the label says "openai" whatever
        # the campaign actually chose - and llm_provider_used is what costing
        # looks a rate up by, so an OpenRouter call was being priced at OpenAI's
        # rates and shown as OpenAI on the call page.
        #
        # What this still cannot tell you is WHICH LEG answered when a fallback
        # is configured and both legs are openai-compatible: the label is
        # identical for both. Recording the configured primary is the honest
        # approximation, and this comment is the rest of the truth.
        if layer == "llm" and name == "openai":
            name = getattr(cfg, "llm_provider", None) or "openai"
        providers_used[layer].add(name)

    @session.on("metrics_collected")
    def _on_metrics(ev):
        try:
            m = ev.metrics
            if getattr(m, "request_id", None) in warm_requests:
                # The connection warm-up behind the greeting. Not a turn, not
                # the caller's, and not the greeting's either.
                return
            usage.collect(m)
            n = type(m).__name__
            if n == "EOUMetrics":
                pending["eou_ms"] = int(m.end_of_utterance_delay * 1000)
                pending["stt_ms"] = int(m.transcription_delay * 1000)
            elif n == "STTMetrics":
                _note_provider("stt", getattr(m, "label", None))
            elif n == "LLMMetrics":
                _note_provider("llm", getattr(m, "label", None))
                # A generation that was thrown away never reached the caller,
                # and its timing explains nothing about the answer that did.
                #
                # preemptive_generation starts the model on a partial transcript
                # before the turn is confirmed. A caller who speaks in pieces -
                # a phrase, a pause, more - gets one generation per piece, and
                # all but the last are cancelled. Call 646, seq 19: FOUR
                # generations in three seconds, three cancelled, and the ttft
                # recorded was the FIRST cancelled one's. The turn read
                # llm_ttft=2494 against a wait of 4678 and 1.6 s that no number
                # on the row could account for.
                #
                # Counted rather than silently dropped: a turn that took four
                # attempts is a turn where the caller was talking in fragments,
                # and that is worth seeing on the row it happened on.
                #
                # Only the TIMING is skipped. The tokens below are still
                # counted: a cancelled generation was billed and spent this
                # call's prompt budget exactly like one that spoke.
                if getattr(m, "cancelled", False):
                    pending["llm_dropped"] = pending.get("llm_dropped", 0) + 1
                else:
                    # A tool call produces two LLM generations; keep the first
                    # TTFT, which is when the model started answering at all.
                    pending.setdefault("llm_ttft_ms", int(m.ttft * 1000))
                pending["prompt_tokens"] = getattr(m, "prompt_tokens", 0)
                pending["cached_tokens"] = getattr(m, "prompt_cached_tokens", 0)
                agent.prompt_tokens += getattr(m, "prompt_tokens", 0)
                if agent.prompt_tokens > cfg.max_prompt_tokens and not agent.limit_hit:
                    asyncio.create_task(enforce_limit(
                        f"max_prompt_tokens={cfg.max_prompt_tokens} "
                        f"(used {agent.prompt_tokens})"))
            elif n == "TTSMetrics":
                _note_provider("tts", getattr(m, "label", None))
                pending["tts_ttfb_ms"] = int(m.ttfb * 1000)
        except Exception:
            logger.exception("metrics handler failed")

    @session.on("conversation_item_added")
    def _on_item(ev):
        nonlocal seq, last_activity
        try:
            item = ev.item
            role = getattr(item, "role", "?")
            # livekit sets this on an agent turn a barge-in cut short.
            interrupted = bool(getattr(item, "interrupted", False))
            text = getattr(item, "text_content", None) or str(getattr(item, "content", ""))
            t = dict(pending) if role == "assistant" else {}

            # The caller's real wait, from livekit's own per-message metrics:
            # e2e_latency is this answer's first audio minus the caller's last
            # word, and it includes what total_ms leaves out - a search, the
            # second model call after it, the fillers the answer queued behind.
            m = getattr(item, "metrics", None) or {}
            wait_ms, timeline = _wait_and_timeline(agent, role, m)
            if wait_ms is not None:
                t["wait_ms"] = wait_ms

            # An empty item with nothing measured is not a turn. One arrives at
            # the start of every session and was being written down anyway,
            # putting a "(no transcript)" caller line at the top of every
            # transcript in the console - visible on 290 calls before anyone
            # asked what it was.
            #
            # Timings are checked too, not just the text: an agent turn cut off
            # by a barge-in has no text and is worth keeping.
            if not (text or "").strip() and not t and not interrupted:
                return

            seq += 1
            extra = ""
            if t:
                # eou ALREADY includes stt; summing all four double-counts.
                t["total_ms"] = (t.get("eou_ms", 0) + t.get("llm_ttft_ms", 0)
                                 + t.get("tts_ttfb_ms", 0))
                extra = (f"  prompt={t.pop('prompt_tokens', 0)}tok"
                         f"  cached={t.pop('cached_tokens', 0)}")
                # Generations this turn started and threw away - see the
                # metrics handler. Paid for, never heard, and the reason a
                # turn's numbers can fail to add up to its wait.
                dropped = t.pop("llm_dropped", 0)
                if dropped:
                    extra += f"  llm_dropped={dropped}"
                if agent.last_kb_ms:
                    extra += f"  kb_tool={agent.last_kb_ms}ms"
                    agent.last_kb_ms = 0
                if agent.last_kb_hits:
                    # Two parallel arrays because that is what the column, the
                    # endpoint and the console already agreed on. Same order.
                    t["kb_chunk_ids"] = [c for c, _ in agent.last_kb_hits]
                    t["kb_scores"] = [sc for _, sc in agent.last_kb_hits]
                    extra += f"  kb_sources={len(agent.last_kb_hits)}"
                    agent.last_kb_hits = []
                pending.clear()

            if role == "assistant":
                # Independently of the state machine. If the agent said
                # something, the caller has not gone quiet - and a flag that
                # flips at the wrong moment must not be the only thing standing
                # between them and being talked over.
                last_activity = time.monotonic()
                agent.turn_count += 1
                if agent.turn_count >= cfg.max_turns and not agent.limit_hit:
                    asyncio.create_task(enforce_limit(f"max_turns={cfg.max_turns}"))

            bits = "  ".join(f"{k[:-3]}={v}ms" for k, v in t.items() if k.endswith("_ms"))
            logger.info("[%-9s] %s%s%s", role, text,
                        "  (interrupted)" if interrupted else "",
                        f"\n            {bits}{extra}" if bits else "")
            # Named, not matched on a suffix. log_turn has read kb_chunk_ids
            # and kb_scores since the table was created, the API has returned
            # them and the console has rendered them - and this comprehension,
            # keeping only keys ending in "_ms", dropped them on the floor every
            # time. Four working pieces and one silent filter in the middle.
            asyncio.create_task(store.log_turn(
                call_id, seq, "agent" if role == "assistant" else "user", text,
                # On its own, not left to the comprehension below - which keeps
                # only timing and kb keys and dropped this on every turn. The
                # column existed, log_turn wrote it and the console rendered it,
                # so every turn recorded until now says it was not interrupted.
                interrupted=interrupted,
                timeline=timeline,
                **{k: v for k, v in t.items()
                   if k.endswith("_ms") or k in ("kb_chunk_ids", "kb_scores")}))
        except Exception:
            logger.exception("transcript handler failed")

    # A provider failing hard is not a completed call. Without this the row is
    # recorded as "completed" and the error_rate alert - which keys on
    # end_reason = 'error' - never fires. An entire Sarvam outage went through
    # as ten clean calls, which is how it stayed invisible.
    session_error: dict[str, str] = {}
    # The same failure in columns: which leg, whose service, what status code.
    # Written to call_errors, where it can be counted and alerted on.
    error_detail: dict = {}

    def _on_error(ev) -> None:
        # Keep the first: it usually causes the rest, and the later ones are
        # noise from the teardown.
        if session_error:
            return
        err = getattr(ev, "error", ev)
        session_error["source"] = type(err).__name__
        session_error["message"] = str(err)[:400]
        # Kept in its own dict, not merged: `source` means the exception class
        # in one and the pipeline leg in the other, and merging them would
        # quietly change what lands in calls.outcome.
        error_detail.update(_error_details(ev))
        logger.error("session error (%s, provider=%s, code=%s): %s",
                     session_error["source"], error_detail.get("provider"),
                     error_detail.get("code"), session_error["message"])

    session.on("error", _on_error)

    async def _shutdown():
        try:
            summary = usage.get_summary()
            u = summary.__dict__ if hasattr(summary, "__dict__") else dict(summary)
            logger.info("usage: %s  turns=%d  kb_tools=%d  limit=%s  transferred=%s  error=%s",
                        summary, agent.turn_count, agent.tool_calls,
                        agent.limit_hit, agent.transferred,
                        session_error.get("source"))

            # transferred and limit are deliberate outcomes and outrank an error
            # seen on the way out; anything else that errored did not complete.
            # ended_by outranks an error seen on the way out for the same reason
            # transferred and limit do: it is a decision, not a failure. A call
            # closed because nobody ever spoke is not an error to investigate.
            reason = ("transferred" if agent.transferred
                      else "limit" if agent.limit_hit
                      else agent.ended_by if agent.ended_by
                      else "error" if session_error
                      else "completed")
            await store.end_call_usage(
                call_id, reason, agent.limit_hit, agent.turn_count, u,
                providers={k: ",".join(sorted(v)) for k, v in providers_used.items() if v},
                # From the config this call ran with, not from the row - the
                # config can be edited between the call and anyone reading it.
                models={"llm": cfg.llm_model, "stt": cfg.stt_model,
                        "tts": cfg.tts_model})
            if agent.transferred:
                dest, why = agent.transferred
                await (await store.pool()).execute(
                    "UPDATE calls SET transferred_to=$2, transfer_reason=$3, outcome=$3 "
                    "WHERE id=$1", call_id, dest, why)
            elif agent.transfer_refused:
                # Not an error and not an outcome - the call carried on. Only
                # that a person was asked for and could not be given.
                await (await store.pool()).execute(
                    "UPDATE calls SET transfer_refused=$2 WHERE id=$1",
                    call_id, agent.transfer_refused)
            elif session_error:
                # The message goes in outcome so the console shows WHY, not just
                # that something went wrong.
                await (await store.pool()).execute(
                    "UPDATE calls SET outcome=$2 WHERE id=$1", call_id,
                    f"{session_error['source']}: {session_error['message']}")

            if error_detail:
                # Its own row rather than more columns on `calls`: one call can
                # lose STT and then lose the LLM, and the second failure is
                # usually the consequence of the first. Keeping both is what
                # makes the order readable afterwards.
                # tenant and campaign are taken from the CALL row rather than
                # from cfg, which carries campaign_id and no tenant at all. A
                # NULL tenant here would be invisible to the alert, which is
                # scoped by tenant - the rows would exist and nothing would
                # ever read them.
                await (await store.pool()).execute(
                    """INSERT INTO call_errors (call_id, tenant_id, campaign_id,
                                                source, provider, code, message)
                       SELECT c.id, c.tenant_id, c.campaign_id, $2, $3, $4, $5
                         FROM calls c WHERE c.id = $1""",
                    call_id, error_detail["source"],
                    error_detail.get("provider"), error_detail.get("code"),
                    error_detail["message"])

            # Not fatal - the call carried on, on lexical matching alone -
            # but it IS a provider failure, and the alert rule counts rows in
            # this table. A broken embedder makes one row per call, which is
            # exactly the shape that rule was built to notice.
            if getattr(agent, "kb_degraded", None):
                await (await store.pool()).execute(
                    """INSERT INTO call_errors (call_id, tenant_id, campaign_id,
                                                source, provider, code, message)
                       SELECT c.id, c.tenant_id, c.campaign_id,
                              'kb', 'openai', NULL, $2
                         FROM calls c WHERE c.id = $1""",
                    call_id,
                    f"knowledge base ran without embeddings: {agent.kb_degraded}")

            # Independent of how the call ended: what the caller SPOKE is not
            # part of the transfer outcome.
            if agent.language_chars:
                await (await store.pool()).execute(
                    "UPDATE calls SET detected_languages = $2::jsonb WHERE id = $1",
                    call_id, json.dumps(agent.language_chars))

            # Queued here, at the very end, and never during the call: the
            # extraction is an LLM round trip and nothing is worth adding to a
            # turn budget that took a day of measurement to bring down.
            #
            # Before store.close(), obviously - and inside the same try, so a
            # failure to queue is logged rather than taking the whole shutdown
            # with it and losing the usage figures too.
            await _queue_postback(store, cfg, call_id, keys, dialler)

            await store.close()
        except Exception:
            logger.exception("shutdown failed")

    ctx.add_shutdown_callback(_shutdown)
    await session.start(room=ctx.room, agent=agent,
                        room_input_options=RoomInputOptions())
    # This is the moment livekit-sip has been waiting for. It holds the INVITE
    # at 180 Ringing until an agent subscribes to the caller's track, which
    # session.start is what does - so every millisecond above this line is
    # ringing the caller hears, and it is not livekit's: its own signalling
    # measures 5 ms invite-to-ringing and 43 ms to the room.
    logger.info("TIMING session_started=%dms  warm_done=%s", since(), warm.done())

    async def _speak_greeting(text: str, **kw) -> None:
        """The greeting, in full, whatever the caller does.

        Never interruptible, whichever way the campaign's barge-in is set. That
        setting is right for the conversation and wrong here: it let a caller's
        "hello" cut the greeting off - and the recording notice with it, since
        the two are one utterance.

        Awaiting say() waits for playout (SpeechHandle.__await__ is
        wait_for_playout), so the flag is set when the caller has HEARD it, not
        when it was queued.

        In `finally`: if this raises and the flag is never set, the agent refuses
        to answer for the rest of the call. The gate's ceiling covers that too;
        this is the first line of defence, not the only one.
        """
        try:
            await session.say(text, allow_interruptions=False, **kw)
        finally:
            agent.greeting_done = True

    # One utterance, not two. Said separately, a caller who speaks over the
    # greeting cancels what follows - and what follows is the recording notice.
    # Joined here it is either both or neither.
    # Which of the two greetings this call gets.
    #
    # The dialler sends a name on about 5% of calls - measured, see migration
    # 053 - so the greeting that does not use one is the common case rather than
    # the exception. A pipe default cannot cover it: swapping a word into "क्या
    # मेरी बात {{cus_name|आप}} से हो रही है?" makes a sentence nobody would say,
    # where what is wanted is to ask for the name instead of using it.
    greeting_tpl = cfg.greeting
    fallback = (getattr(cfg, "greeting_fallback", None) or "").strip()
    missing = prompt_mod.unfilled(cfg.greeting, dialler) if fallback else []
    if missing:
        greeting_tpl = fallback
        logger.info("greeting: using the alternative (no %s on this call)",
                    ", ".join(missing))

    opening = " ".join(x for x in (_render(greeting_tpl, dialler),
                                   cfg.recording_disclosure) if x)
    if not opening:
        # Nothing to say, so nothing to wait for. Without this a campaign with no
        # opening would never lift the gate, and would answer nothing at all.
        agent.greeting_done = True
    else:
        # Cacheable only when the greeting does not depend on who is calling. A
        # placeholder gives every caller a different opening, and a cache with
        # one entry per caller is not a cache - it is a disk leak.
        #
        # Decided on the greeting CHOSEN, not on cfg.greeting - which is what it
        # used to read, and why a campaign with a personalised greeting rendered
        # 7.2 seconds of speech live on every single call, including the 95%
        # that never had a name to insert. Those now come from the cache, and
        # the TTS connection warms up behind the greeting instead of landing on
        # the caller's first question.
        cache_path = None
        cached = None
        if "{{" not in (greeting_tpl or ""):
            cache_path = greeting_cache.path_for(
                opening, cfg.tts_provider, cfg.tts_model, cfg.tts_voice)
            cached = greeting_cache.frames(cache_path)

        # `audio=` hands say() the rendered frames; the text still goes to the
        # transcript and the model exactly as before, so nothing downstream can
        # tell the difference.
        if cached is not None:
            # Started before the greeting, not after: the greeting is the only
            # thing standing between the caller and a cold TTS, and it is 7.2
            # seconds long. Nothing else in the call is a better place to spend
            # a connection handshake.
            asyncio.create_task(_warm_tts(tts_stack, warm_requests))
            await _speak_greeting(opening, audio=cached)
        else:
            await _speak_greeting(opening)
            if cache_path is not None:
                # Started after the greeting, not before it: this call has
                # already paid for a connection and there is no sense competing
                # with it for the one thing the caller is waiting on.
                asyncio.create_task(
                    greeting_cache.store(tts_stack, opening, cache_path))

    if getattr(cfg, "reply_filler_enabled", False):
        # After the greeting, not before it: the greeting has the TTS to itself,
        # and the caller is about to answer it, so nothing is waiting on this.
        asyncio.create_task(_warm_fillers(cfg, tts_stack))


if __name__ == "__main__":
    # Step 7 measured a 2.3 s cold spawn on the first job; dev mode defaults
    # num_idle_processes to 0, which is why the first call after starting the
    # worker just rang.
    import inspect
    _kw = {"entrypoint_fnc": entrypoint, "prewarm_fnc": prewarm}
    _p = inspect.signature(WorkerOptions.__init__).parameters
    if "num_idle_processes" in _p:
        # A bigger warm buffer means fewer processes are spawned at once when
        # calls arrive together. Each spawn loads Silero + onnxruntime, and it is
        # those spikes - not sustained load - that were tripping the threshold.
        _kw["num_idle_processes"] = int(os.getenv("NUM_IDLE_PROCESSES", "6"))
    if "port" in _p:
        # prod_default is a FIXED 8081 (dev_default is 0 = random), so every
        # extra worker instance on this box collides:
        #   OSError [Errno 98] address already in use -> worker exits at startup
        # systemd's Restart=always then hides it: the unit reads "active" while
        # the worker is actually crash-looping and never registers.
        _kw["port"] = int(os.getenv("AGENT_HTTP_PORT", "8081"))
    if "load_fnc" in _p:
        # THE number that decides how many concurrent calls this box can take.
        #
        # By default livekit-agents reports psutil.cpu_percent() - system-wide
        # CPU, clamped to 1.0 - and LiveKit's worker selection weights each
        # worker by `max(0, 1 - load)` (pkg/service/agentservice.go). A worker
        # busy on one call pins a core, the metric saturates at 1.0, its weight
        # becomes 0, and the server answers "no workers with sufficient
        # capacity" while 30 of 32 cores sit idle. Every worker on the box
        # reports the same system-wide figure, so they all lose their weight at
        # the same instant - which is why running 1, 3 or 6 workers made no
        # difference to the ceiling.
        #
        # CPU is the wrong meter for this workload: STT, LLM and TTS are network
        # calls, and a live conversation spends most of its time waiting. Report
        # what actually limits us instead - how many calls this worker is
        # already carrying. Load reaches 1.0 at MAX_JOBS_PER_WORKER, which makes
        # that env var a real, enforced concurrency cap rather than a side
        # effect of how busy the CPU happened to look.
        _max_jobs = max(1, int(os.getenv("MAX_JOBS_PER_WORKER", "10")))

        def _job_count_load(worker) -> float:
            return min(len(worker.active_jobs) / _max_jobs, 1.0)

        _kw["load_fnc"] = _job_count_load
    if "load_threshold" in _p:
        # Now that load is a job count, 1.0 is the honest threshold: it makes the
        # worker's own _is_available() agree with the server's weighting instead
        # of guarding a different quantity. It used to be tuned against the CPU
        # metric (0.9, then 5.0, then inf) - none of which mattered, because the
        # server never consults this gate; it reads the reported load directly.
        _kw["load_threshold"] = float(os.getenv("LOAD_THRESHOLD", "1.0"))
    if "drain_timeout" in _p:
        _kw["drain_timeout"] = int(os.getenv("DRAIN_TIMEOUT", "150"))
    if "shutdown_process_timeout" in _p:
        # THE DEADLINE THAT SILENTLY LOST A CALL'S POSTBACK.
        #
        # The default is 10.0 seconds, and it is not a timeout in the Python
        # sense - nothing raises, nothing is caught, nothing is logged by us.
        # livekit waits this long for the job process to finish its shutdown
        # callbacks and then kills it outright: SIGUSR1, exit code -10, no
        # traceback. Call 590, a 15-turn conversation on a gateway model:
        #
        #   09:44:36.055  process exiting
        #   09:44:36.054  usage: ... turns=15          <- our shutdown started
        #   09:44:46.056  process did not exit in time, killing process
        #   09:44:46.094  process exited with non-zero exit code -10
        #
        # Ten seconds to the millisecond. The last thing _shutdown does is
        # _queue_postback, whose extraction is an LLM round trip; it ran past
        # the deadline, the process was killed mid-request, and the row was
        # never written. The console showed a completed call with no delivery
        # log and no error - the customer's system simply never heard about it.
        # Days of it would have looked like a bug in the postback code.
        #
        # 45 seconds, paired with POSTBACK_EXTRACT_TIMEOUT=20 in postback.py.
        # The pair is the point: the extraction must give up with enough room
        # left to build the envelope and INSERT the row, because a postback
        # without the extracted fields is still worth having and one that was
        # never written is not. Raising this alone would only move the cliff.
        #
        # Costs nothing when things are healthy - a normal shutdown takes about
        # four seconds and this is only reached when something is stuck. Kept
        # below systemd's 90 s TimeoutStopSec so a restart still ends in a stop
        # rather than a kill.
        _kw["shutdown_process_timeout"] = float(
            os.getenv("SHUTDOWN_PROCESS_TIMEOUT", "45"))
    cli.run_app(WorkerOptions(**_kw))
