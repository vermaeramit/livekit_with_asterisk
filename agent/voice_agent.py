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
import logging
import os
import re
import time
import zoneinfo

import aiohttp

from livekit import api
from livekit.agents import (
    Agent, AgentSession, JobContext, JobProcess, RoomInputOptions, RunContext,
    WorkerOptions, cli, function_tool, metrics,
)
# aliased: bare stt/tts/llm would shadow the local variables of the same name
from livekit.agents import llm as lk_llm, stt as lk_stt, tts as lk_tts
from livekit.plugins import google, openai, sarvam, silero, soniox

import greeting_cache
import prompt as prompt_mod
import tools as tools_mod

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
    proc.userdata["vad"] = silero.VAD.load()
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

    logger.info("prewarm complete: VAD %.0f ms, imports %.0f ms",
                vad_ms, imports_ms)


def _stt_kwargs(cfg):
    """Sarvam STT options, tunable from env.

    Sarvam's SERVER-side VAD decides END_SPEECH and only then does the plugin
    flush - measured ~700 ms behind the local Silero VAD. high_vad_sensitivity
    removes almost all of it. Note saarika:* has supports_vad_params=False, so
    negative_frames_count and friends are silently dropped; flush_signal measured
    as a no-op.
    """
    kw = {"language": cfg.language,
          "model": os.getenv("SARVAM_STT_MODEL") or cfg.stt_model or "saarika:v2.5"}
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
    kw = {"target_language_code": cfg.language, "model": cfg.tts_model or "bulbul:v3"}
    voice = os.getenv("SARVAM_TTS_VOICE") or cfg.tts_voice
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
_PROMPT_ATTRS = {
    "dialer.cus_name": "Caller name",
    "dialer.modalname": "Product they own",
    "dialer.calltype": "Call type",
}
_RECORD_ONLY_ATTRS = ("dialer.lead_id", "dialer.sr_id", "dialer.call_unique",
                      "dialer.language")


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
    # This is the STORAGE side only. What reaches the model stays curated, in
    # _PROMPT_ATTRS: a model handed a lead id will eventually read it out to the
    # caller, and that must not become automatic.
    return {k: v for k, raw in attrs.items()
            if k.startswith("dialer.") and (v := (raw or "").strip())}


def _caller_context(dialler: dict[str, str]):
    """-> a ChatContext carrying the caller context, or None.

    A SEPARATE message, never appended to `instructions`. The instructions are
    the cacheable prefix - byte-identical across every call on a campaign, which
    is what earns OpenAI's prompt cache (measured 1198 ms cold against 805 ms
    warm). Putting a caller's name into them would make every call's prefix
    unique and the cache would never hit again, silently.
    """
    lines = [f"- {label}: {dialler[key]}"
             for key, label in _PROMPT_ATTRS.items() if dialler.get(key)]
    if not lines:
        return None
    body = "\n".join(lines)
    c = lk_llm.ChatContext.empty()
    c.add_message(
        role="system",
        content=(
            "CALLER CONTEXT, provided by the dialling system before the call "
            "connected. It is reliable - use it rather than asking the caller "
            "to repeat what we already know.\n"
            f"{body}\n\n"
            "Greet them by name once, naturally, and do not read any of this "
            "back as a list."
        ),
    )
    return c


# {{cus_name}} / {{modalname|आपकी गाड़ी}} in anything spoken to the caller.
#
# A default after the pipe is not decoration. The dialler does not always send
# every field - X-language arrives empty today - and a greeting that renders as
# "क्या मेरी बात  जी से हो रही है?" is worse than one that never used the name.
# Without a default the placeholder becomes empty and the double space is
# collapsed, which is the least bad of the remaining options.
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_]+)\s*(?:\|([^}]*))?\}\}")


def _render(template: str | None, dialler: dict[str, str]) -> str | None:
    """Substitute dialler context into a spoken string.

    Only ever applied to things SAID to the caller - greeting, transfer and
    limit messages. Never to `instructions`: those are the cacheable prompt
    prefix, and a caller's name inside them would make every call's prefix
    unique and silently kill the prompt cache.
    """
    if not template or "{{" not in template:
        return template

    def one(m: re.Match) -> str:
        key, default = m.group(1), (m.group(2) or "")
        return (dialler.get(f"dialer.{key}") or default).strip()

    return re.sub(r"\s{2,}", " ", _PLACEHOLDER.sub(one, template)).strip()


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
        self.transfer_asked_at: int | None = None

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

    def _markers(self) -> dict[str, str]:
        """{marker text -> the flag it sets}, empties dropped."""
        out = {}
        if self.cfg.end_call_marker:
            out[self.cfg.end_call_marker] = "end_requested"
        if self.cfg.transfer_marker and self.cfg.transfer_enabled:
            out[self.cfg.transfer_marker] = "transfer_requested"
        return out

    async def stt_node(self, audio, model_settings):
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
            async for frame in Agent.default.tts_node(self, text, model_settings):
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

        async for frame in Agent.default.tts_node(self, filtered(), model_settings):
            yield frame

    # ────────────────────────── knowledge ──────────────────────────

    @function_tool
    async def search_knowledge_base(self, query: str) -> str:
        """Look up details in the company documents.

        Use this ONLY when the REFERENCE INFORMATION in your instructions does not
        answer the caller's question.

        Args:
            query: A short search query in ENGLISH describing what to find, e.g.
                "cancellation policy refund", "baggage allowance". Always English,
                even when the caller speaks another language - the documents are
                in English and an English query matches them far better.
        """
        import kb
        self.tool_calls += 1
        t0 = time.perf_counter()
        try:
            hits = await kb.search(query, self.cfg.name,
                                   self.cfg.kb_top_k, self.cfg.kb_min_score,
                                   api_key=self.keys.get("openai"))
        except Exception:
            logger.exception("kb search failed")
            return "The knowledge base is unavailable right now."
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
        if not hits:
            # Say it explicitly. Returning an empty string reads as permission to
            # answer from general knowledge - that is where invented phone numbers
            # come from.
            return ("No relevant information found in the documents. "
                    "Tell the caller you do not have that information.")
        return kb.format_context(hits)

    # ────────────────────────── handoff ──────────────────────────

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

        logger.info("  TRANSFER(%r) -> %s  participant=%s",
                    reason, self.cfg.transfer_to, sip_identity)
        lkapi = api.LiveKitAPI(url=_api_url(),
                               api_key=os.environ["LIVEKIT_API_KEY"],
                               api_secret=os.environ["LIVEKIT_API_SECRET"])
        try:
            await lkapi.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    participant_identity=sip_identity,
                    room_name=self.room.name,
                    transfer_to=self.cfg.transfer_to,
                    play_dialtone=False,
                ))
            self.transferred = (self.cfg.transfer_to, reason)
            logger.info("  TRANSFER OK -> %s", self.cfg.transfer_to)
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
_TTS_NATIVE_RATE = {"sarvam": 22050, "openai": 24000, "soniox": 24000}


def _soniox_lang(language: str) -> str:
    """Soniox takes bare ISO codes ("hi"); the config carries Sarvam's regional
    form ("hi-IN"). Passing hi-IN through is not an error the API reports - it
    just synthesises something else."""
    return (language or "en").split("-")[0]


def _build_stt(provider: str, cfg, key: str, use_config_model: bool):
    """use_config_model is False for a fallback leg: cfg.stt_model names a model
    that belongs to the PRIMARY provider, and handing Sarvam's 'saarika:v2.5' to
    OpenAI fails at the first utterance rather than at startup."""
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
        return soniox.STT(api_key=key, params=soniox.STTOptions(**opts))
    raise ValueError(f"unknown STT provider '{provider}'")


def _build_tts(provider: str, cfg, key: str, use_config_model: bool):
    if provider == "sarvam":
        kw = _tts_kwargs(cfg)
        if not use_config_model:
            kw["model"], kw["speaker"] = "bulbul:v3", "shubh"
        return sarvam.TTS(**kw, api_key=key)
    if provider == "openai":
        model = (cfg.tts_model if use_config_model else None) or "gpt-4o-mini-tts"
        return openai.TTS(model=model, api_key=key)
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
            model=(cfg.tts_model if use_config_model else None) or "tts-rt-v2",
            language=_soniox_lang(cfg.language),
            voice=(cfg.tts_voice if use_config_model else None) or "Priya",
            sample_rate=_TTS_NATIVE_RATE["soniox"],
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
    if not keys.get(configured):
        logger.warning("%s fallback '%s' has no key for this campaign - "
                       "running on %s alone", layer, configured, primary)
        return None
    return configured


def _stt_stack(cfg, vad, keys: dict):
    primary = _build_stt(cfg.stt_provider, cfg, keys[cfg.stt_provider], True)
    fb = _fallback_provider("stt", cfg.stt_fallback_provider, cfg.stt_provider, keys)
    if not fb:
        return primary
    # vad is required: gpt-4o-mini-transcribe is not a streaming STT, so without
    # a VAD to chunk the audio it has nothing to send.
    return lk_stt.FallbackAdapter(
        [primary, _build_stt(fb, cfg, keys[fb], False)],
        vad=vad, attempt_timeout=ATTEMPT_TIMEOUT)


def _tts_stack(cfg, keys: dict):
    primary = _build_tts(cfg.tts_provider, cfg, keys[cfg.tts_provider], True)
    fb = _fallback_provider("tts", cfg.tts_fallback_provider, cfg.tts_provider, keys)
    if not fb:
        return primary
    # Note there is no attempt_timeout on the TTS adapter - unlike STT and LLM.
    return lk_tts.FallbackAdapter(
        [primary, _build_tts(fb, cfg, keys[fb], False)],
        sample_rate=_TTS_NATIVE_RATE.get(cfg.tts_provider, 24000))


def _llm_stack(cfg, keys: dict):
    primary = openai.LLM(model=cfg.llm_model, temperature=cfg.llm_temperature,
                         prompt_cache_key=cfg.name, api_key=keys["openai"])
    if not FALLBACK:
        return primary
    # The only layer with real provider diversity: gemini-flash-lite matches the
    # primary's latency, so a full OpenAI outage costs speech and hearing but
    # not thought.
    #
    # Gemini stays on the PLATFORM credentials: it authenticates with a service
    # account JSON file, not a key string, and there is nowhere in the console to
    # put a file. So the fallback leg is ours, not the client's - worth knowing
    # when reading an invoice, and the reason this leg is not offered as a
    # per-client setting.
    return lk_llm.FallbackAdapter(
        [primary, google.LLM(model="gemini-flash-lite-latest",
                             temperature=cfg.llm_temperature)],
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
_PROVIDER_HOSTS = {
    "sarvam": "https://api.sarvam.ai",
    "openai": "https://api.openai.com",
    "soniox": "https://api.soniox.com",
}


def _now_line(tz_name: str | None) -> str:
    """The one line that tells the agent what day it is.

    Spelled out - weekday, month by name, 12-hour clock - because the model has
    to reason with it ("कल" means tomorrow's date, not the string "tomorrow") and
    an ISO stamp invites it to read the digits aloud to the caller.

    An unknown timezone falls back to +05:30, not to UTC. Every caller on this
    system is in India, and a clock silently five and a half hours out looks
    like it is working right up until somebody books a morning appointment.
    """
    tz = None
    if tz_name:
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            logger.warning("unknown timezone %r - using +05:30", tz_name)
    if tz is None:
        tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")
    now = datetime.datetime.now(tz)
    return ("CURRENT DATE AND TIME: "
            + now.strftime("%A, %d %B %Y, %I:%M %p ").replace(" 0", " ")
            + (tz_name or "IST")
            + "\nUse this to work out what the caller means by today, "
              "tomorrow, this evening, next week and so on.")


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
    t = time.perf_counter()
    try:
        async for ev in tts.synthesize("नमस्ते"):
            # Claimed before the stream ends, and the metrics arrive when it
            # ends - so by the time anybody asks, this id is already spoken for.
            seen.add(ev.request_id)
    except Exception:
        logger.debug("tts warm-up failed - the first reply will pay for it",
                     exc_info=True)
        return
    logger.info("TIMING tts_warm=%dms", (time.perf_counter() - t) * 1000)


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

        extracted = await pb.extract(
            turns=turns, fields=fields, api_key=keys.get("openai", ""),
            tool_calls=tool_calls, model=cfg.llm_model)

        payload = pb.envelope(
            call_row=dict(row) if row else {"id": call_id},
            dialler=dialler,
            extracted=extracted,
            turns=turns if cfg.postback_include_transcript else None,
            tool_calls=tool_calls)

        await store.save_postback(call_id, cfg.campaign_id, payload)
        logger.info("postback queued for call %s (%d extracted fields)",
                    call_id, len(extracted))
    except Exception:
        logger.exception("postback could not be prepared for call %s", call_id)


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
    if cfg.campaign_id is not None:
        keys = await store.load_provider_keys(cfg.campaign_id)
        # Whichever providers THIS campaign actually uses - not a fixed pair.
        # openai is always in the set: the LLM runs on it, and so does knowledge
        # base retrieval, whatever STT and TTS are set to.
        needed = {cfg.stt_provider, cfg.tts_provider, "openai"}
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
        instructions += "\n\n" + _now_line(getattr(cfg, "prompt_timezone", None))

    logger.info("config=%s lang=%s llm=%s kb=%s(%s, %d tok) transfer=%s->%s",
                cfg.name, cfg.language, cfg.llm_model, cfg.kb_enabled, kb_mode,
                kb_tokens, cfg.transfer_enabled, cfg.transfer_to)
    logger.info("TIMING config+keys+prompt=%dms", since())

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
        await store.end_call_if_open(
            call_id, "error", "the job failed before the session started")

    ctx.add_shutdown_callback(_safety_net)

    # Read LAST, not with the other attributes above. headers_to_attributes is
    # populated asynchronously by livekit-sip, and everything between the two
    # points - the config load, the prompt build, start_call - has given it time
    # to land. Logged either way, so a campaign that should have context and does
    # not is visible rather than merely quieter.
    dialler = _dialler_attrs(sip_participant)
    if dialler:
        logger.info("dialler context: %s",
                    " ".join(f"{k.split('.', 1)[1]}={v}" for k, v in dialler.items()))
        await store.set_dialler_context(call_id, dialler)
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
        await session.say(line, allow_interruptions=True)

    extra_tools = []
    if cfg.campaign_id is not None:
        specs = await store.load_tools(cfg.campaign_id)
        if specs:
            extra_tools = tools_mod.build_all(
                specs, call_id,
                functools.partial(store.record_tool_call, call_id),
                _tool_says)
            logger.info("campaign tools: %s",
                        ", ".join(s["name"] for s in specs))

    agent = KBAgent(instructions, cfg, kb_mode, ctx.room, keys,
                    chat_ctx=_caller_context(dialler),
                    extra_tools=extra_tools)

    vad = ctx.proc.userdata["vad"]
    # Held rather than passed inline: the greeting cache renders through this
    # same stack, so a campaign's fallback provider applies there too.
    tts_stack = _tts_stack(cfg, keys)
    session = AgentSession(
        stt=_stt_stack(cfg, vad, keys),
        llm=_llm_stack(cfg, keys),
        tts=tts_stack,
        vad=vad,
        turn_detection=MultilingualModel(),
        allow_interruptions=cfg.allow_interrupt,
        min_endpointing_delay=MIN_ENDPOINTING,
        max_endpointing_delay=MAX_ENDPOINTING,
    )

    live["session"] = session

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
    agent_speaking = False
    silence_attempts = 0
    # Set the moment the session ends, whatever ended it. The watchdog's other
    # exit conditions - a limit, a transfer - do not cover the ordinary case of
    # somebody hanging up.
    closed = False

    @session.on("close")
    def _on_close(_ev=None):
        nonlocal closed
        closed = True

    @session.on("agent_state_changed")
    def _on_agent_state(ev):
        nonlocal agent_speaking, last_activity
        state = getattr(ev, "new_state", None)
        agent_speaking = state == "speaking"
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
            if agent_speaking:
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
            logger.info("silence %ds - prompt %d/%d%s", timeout,
                        silence_attempts, len(prompts),
                        " (last)" if final else "")
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
                # a tool call produces two LLM turns; keep the first TTFT
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
        nonlocal seq
        try:
            item = ev.item
            role = getattr(item, "role", "?")
            text = getattr(item, "text_content", None) or str(getattr(item, "content", ""))
            t = dict(pending) if role == "assistant" else {}

            # An empty item with nothing measured is not a turn. One arrives at
            # the start of every session and was being written down anyway,
            # putting a "(no transcript)" caller line at the top of every
            # transcript in the console - visible on 290 calls before anyone
            # asked what it was.
            #
            # Timings are checked too, not just the text: an agent turn cut off
            # by a barge-in has no text and is worth keeping.
            if not (text or "").strip() and not t:
                return

            seq += 1
            extra = ""
            if t:
                # eou ALREADY includes stt; summing all four double-counts.
                t["total_ms"] = (t.get("eou_ms", 0) + t.get("llm_ttft_ms", 0)
                                 + t.get("tts_ttfb_ms", 0))
                extra = (f"  prompt={t.pop('prompt_tokens', 0)}tok"
                         f"  cached={t.pop('cached_tokens', 0)}")
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
                agent.turn_count += 1
                if agent.turn_count >= cfg.max_turns and not agent.limit_hit:
                    asyncio.create_task(enforce_limit(f"max_turns={cfg.max_turns}"))

            bits = "  ".join(f"{k[:-3]}={v}ms" for k, v in t.items() if k.endswith("_ms"))
            logger.info("[%-9s] %s%s", role, text,
                        f"\n            {bits}{extra}" if bits else "")
            # Named, not matched on a suffix. log_turn has read kb_chunk_ids
            # and kb_scores since the table was created, the API has returned
            # them and the console has rendered them - and this comprehension,
            # keeping only keys ending in "_ms", dropped them on the floor every
            # time. Four working pieces and one silent filter in the middle.
            asyncio.create_task(store.log_turn(
                call_id, seq, "agent" if role == "assistant" else "user", text,
                **{k: v for k, v in t.items()
                   if k.endswith("_ms") or k in ("kb_chunk_ids", "kb_scores")}))
        except Exception:
            logger.exception("transcript handler failed")

    # A provider failing hard is not a completed call. Without this the row is
    # recorded as "completed" and the error_rate alert - which keys on
    # end_reason = 'error' - never fires. An entire Sarvam outage went through
    # as ten clean calls, which is how it stayed invisible.
    session_error: dict[str, str] = {}

    def _on_error(ev) -> None:
        # Keep the first: it usually causes the rest, and the later ones are
        # noise from the teardown.
        if session_error:
            return
        err = getattr(ev, "error", ev)
        session_error["source"] = type(err).__name__
        session_error["message"] = str(err)[:400]
        logger.error("session error (%s): %s",
                     session_error["source"], session_error["message"])

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
                providers={k: ",".join(sorted(v)) for k, v in providers_used.items() if v})
            if agent.transferred:
                dest, why = agent.transferred
                await (await store.pool()).execute(
                    "UPDATE calls SET transferred_to=$2, transfer_reason=$3, outcome=$3 "
                    "WHERE id=$1", call_id, dest, why)
            elif session_error:
                # The message goes in outcome so the console shows WHY, not just
                # that something went wrong.
                await (await store.pool()).execute(
                    "UPDATE calls SET outcome=$2 WHERE id=$1", call_id,
                    f"{session_error['source']}: {session_error['message']}")

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

    # One utterance, not two. Said separately, a caller who speaks over the
    # greeting cancels what follows - and what follows is the recording notice.
    # Joined here it is either both or neither.
    opening = " ".join(x for x in (_render(cfg.greeting, dialler),
                                   cfg.recording_disclosure) if x)
    if opening:
        # Cacheable only when the greeting does not depend on who is calling. A
        # placeholder gives every caller a different opening, and a cache with
        # one entry per caller is not a cache - it is a disk leak.
        cache_path = None
        cached = None
        if "{{" not in (cfg.greeting or ""):
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
            await session.say(opening, audio=cached,
                              allow_interruptions=cfg.allow_interrupt)
        else:
            await session.say(opening, allow_interruptions=cfg.allow_interrupt)
            if cache_path is not None:
                # Started after the greeting, not before it: this call has
                # already paid for a connection and there is no sense competing
                # with it for the one thing the caller is waiting on.
                asyncio.create_task(
                    greeting_cache.store(tts_stack, opening, cache_path))


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
    cli.run_app(WorkerOptions(**_kw))
