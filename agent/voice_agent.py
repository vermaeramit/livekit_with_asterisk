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
import logging
import os
import time

from livekit import api
from livekit.agents import (
    Agent, AgentSession, JobContext, JobProcess, RoomInputOptions, RunContext,
    WorkerOptions, cli, function_tool, metrics,
)
# aliased: bare stt/tts/llm would shadow the local variables of the same name
from livekit.agents import llm as lk_llm, stt as lk_stt, tts as lk_tts
from livekit.plugins import google, openai, sarvam, silero

import prompt as prompt_mod

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


def prewarm(proc: JobProcess):
    """Only VAD belongs here. MultilingualModel needs a job context (it talks to
    the inference executor process) and crashes the worker if loaded in prewarm."""
    t = time.perf_counter()
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("prewarm (VAD) complete in %.0f ms", (time.perf_counter() - t) * 1000)


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


class KBAgent(Agent):
    def __init__(self, instructions: str, cfg, kb_mode: str, room):
        super().__init__(instructions=instructions)
        self.cfg = cfg
        self.kb_mode = kb_mode
        self.room = room
        self.tool_calls = 0
        self.last_kb_ms = 0
        self.transferred: tuple[str, str] | None = None
        self.turn_count = 0
        self.prompt_tokens = 0
        self.limit_hit: str | None = None

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
                                   self.cfg.kb_top_k, self.cfg.kb_min_score)
        except Exception:
            logger.exception("kb search failed")
            return "The knowledge base is unavailable right now."
        self.last_kb_ms = int((time.perf_counter() - t0) * 1000)
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
        if not self.cfg.transfer_enabled:
            return ("Transfer is disabled. Tell the caller to call back during "
                    "office hours.")

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
        try:
            context.disallow_interruptions()
        except Exception:
            pass
        try:
            handle = await context.session.say(msg, allow_interruptions=False)
            await handle.wait_for_playout()
        except Exception:
            logger.exception("handoff announcement failed - transferring anyway")

        logger.info("  TOOL transfer_to_human(%r) -> %s  participant=%s",
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


def _stt_stack(stt_kw: dict, vad):
    primary = sarvam.STT(**stt_kw)
    if not FALLBACK:
        return primary
    # vad is required: gpt-4o-mini-transcribe is not a streaming STT, so without
    # a VAD to chunk the audio it has nothing to send.
    return lk_stt.FallbackAdapter(
        [primary, openai.STT(model="gpt-4o-mini-transcribe")],
        vad=vad, attempt_timeout=ATTEMPT_TIMEOUT)


def _tts_stack(tts_kw: dict):
    primary = sarvam.TTS(**tts_kw)
    if not FALLBACK:
        return primary
    # sample_rate is Sarvam's native 22050 so the PRIMARY path never resamples.
    # Only the fallback pays that cost, and only while it is in use.
    # Note there is no attempt_timeout on the TTS adapter - unlike STT and LLM.
    return lk_tts.FallbackAdapter(
        [primary, openai.TTS(model="gpt-4o-mini-tts")], sample_rate=22050)


def _llm_stack(cfg):
    primary = openai.LLM(model=cfg.llm_model, temperature=cfg.llm_temperature,
                         prompt_cache_key=cfg.name)
    if not FALLBACK:
        return primary
    # The only layer with real provider diversity: gemini-flash-lite matches the
    # primary's latency, so a full OpenAI outage costs speech and hearing but
    # not thought.
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


async def entrypoint(ctx: JobContext):
    import store

    # Connect first: which campaign this call belongs to is decided by the
    # number that was dialled, and that only arrives with the SIP participant.
    # livekit-sip creates the participant before dispatching this job, so it is
    # already there.
    await ctx.connect()

    caller = callee = sip_call_id = None
    for p in ctx.room.remote_participants.values():
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

    stt_kw, tts_kw = _stt_kwargs(cfg), _tts_kwargs(cfg)

    # built in prompt.py so the cache warmer emits a byte-identical prefix
    instructions, kb_mode, kb_tokens = await prompt_mod.build_instructions(cfg)

    logger.info("config=%s lang=%s llm=%s kb=%s(%s, %d tok) transfer=%s->%s",
                cfg.name, cfg.language, cfg.llm_model, cfg.kb_enabled, kb_mode,
                kb_tokens, cfg.transfer_enabled, cfg.transfer_to)

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

    agent = KBAgent(instructions, cfg, kb_mode, ctx.room)

    vad = ctx.proc.userdata["vad"]
    session = AgentSession(
        stt=_stt_stack(stt_kw, vad),
        llm=_llm_stack(cfg),
        tts=_tts_stack(tts_kw),
        vad=vad,
        turn_detection=MultilingualModel(),
        allow_interruptions=cfg.allow_interrupt,
        min_endpointing_delay=MIN_ENDPOINTING,
        max_endpointing_delay=MAX_ENDPOINTING,
    )

    usage = metrics.UsageCollector()
    pending: dict[str, int] = {}
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

    @session.on("metrics_collected")
    def _on_metrics(ev):
        try:
            m = ev.metrics
            usage.collect(m)
            n = type(m).__name__
            if n == "EOUMetrics":
                pending["eou_ms"] = int(m.end_of_utterance_delay * 1000)
                pending["stt_ms"] = int(m.transcription_delay * 1000)
            elif n == "LLMMetrics":
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
                pending["tts_ttfb_ms"] = int(m.ttfb * 1000)
        except Exception:
            logger.exception("metrics handler failed")

    @session.on("conversation_item_added")
    def _on_item(ev):
        nonlocal seq
        seq += 1
        try:
            item = ev.item
            role = getattr(item, "role", "?")
            text = getattr(item, "text_content", None) or str(getattr(item, "content", ""))
            t = dict(pending) if role == "assistant" else {}
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
                pending.clear()

            if role == "assistant":
                agent.turn_count += 1
                if agent.turn_count >= cfg.max_turns and not agent.limit_hit:
                    asyncio.create_task(enforce_limit(f"max_turns={cfg.max_turns}"))

            bits = "  ".join(f"{k[:-3]}={v}ms" for k, v in t.items() if k.endswith("_ms"))
            logger.info("[%-9s] %s%s", role, text,
                        f"\n            {bits}{extra}" if bits else "")
            asyncio.create_task(store.log_turn(
                call_id, seq, "agent" if role == "assistant" else "user", text,
                **{k: v for k, v in t.items() if k.endswith("_ms")}))
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
            reason = ("transferred" if agent.transferred
                      else "limit" if agent.limit_hit
                      else "error" if session_error
                      else "completed")
            await store.end_call_usage(call_id, reason, agent.limit_hit,
                                       agent.turn_count, u)
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
            await store.close()
        except Exception:
            logger.exception("shutdown failed")

    ctx.add_shutdown_callback(_shutdown)
    await session.start(room=ctx.room, agent=agent,
                        room_input_options=RoomInputOptions())
    if cfg.greeting:
        await session.say(cfg.greeting, allow_interruptions=cfg.allow_interrupt)


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
