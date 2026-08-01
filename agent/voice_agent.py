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
from livekit.plugins import openai, sarvam, silero

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


GROUNDING_RULES = """

KNOWLEDGE RULES - these override every other instruction:
- The "REFERENCE INFORMATION" section above is your primary source. Answer from it.
- If it does not answer the question, call search_knowledge_base once, then answer
  from what it returns.
- If neither has the answer, say plainly that you do not have that information.
- Never invent or guess a price, date, phone number, policy, name, or availability.
  A confident wrong number is far worse than admitting you do not know - the caller
  will act on it.
- Do not fill gaps with general knowledge.
"""

TRANSFER_RULES = """

HANDOFF:
- Call transfer_to_human when the caller asks for a person, sounds frustrated,
  wants to complain, or asks something you still cannot answer after searching.
- Do NOT transfer for anything you can answer yourself.
- Do not announce the handoff yourself - the tool speaks the line and moves the
  call. Just call it.
"""


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


async def entrypoint(ctx: JobContext):
    import kb
    import store

    cfg = await store.load_config(CONFIG_NAME)
    stt_kw, tts_kw = _stt_kwargs(cfg), _tts_kwargs(cfg)

    instructions = cfg.instructions
    kb_mode, kb_tokens = "off", 0
    if cfg.kb_enabled:
        text, kb_tokens, kb_mode = await kb.load_inline(cfg.name, cfg.kb_inline_max_tokens)
        if text:
            label = ("REFERENCE INFORMATION" if kb_mode == "full" else
                     "AVAILABLE DOCUMENTS (use search_knowledge_base for details)")
            instructions += f"\n\n=== {label} ===\n{text}\n=== END ===\n"
        instructions += GROUNDING_RULES
    if cfg.transfer_enabled:
        instructions += TRANSFER_RULES

    logger.info("config=%s lang=%s llm=%s kb=%s(%s, %d tok) transfer=%s->%s",
                cfg.name, cfg.language, cfg.llm_model, cfg.kb_enabled, kb_mode,
                kb_tokens, cfg.transfer_enabled, cfg.transfer_to)

    await ctx.connect()

    caller = callee = None
    for p in ctx.room.remote_participants.values():
        caller = caller or _sip_attr(p, "sip.phoneNumber", "sip.from_user")
        callee = callee or _sip_attr(p, "sip.trunkPhoneNumber", "sip.to_user")

    call_id = await store.start_call(ctx.room.name, caller, callee, cfg.name, cfg.language)
    logger.info("call_id=%s caller=%s callee=%s", call_id, caller, callee)

    agent = KBAgent(instructions, cfg, kb_mode, ctx.room)

    session = AgentSession(
        stt=sarvam.STT(**stt_kw),
        llm=openai.LLM(model=cfg.llm_model, temperature=cfg.llm_temperature),
        tts=sarvam.TTS(**tts_kw),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
        allow_interruptions=cfg.allow_interrupt,
        min_endpointing_delay=MIN_ENDPOINTING,
        max_endpointing_delay=MAX_ENDPOINTING,
    )

    usage = metrics.UsageCollector()
    pending: dict[str, int] = {}
    seq = 0

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

            bits = "  ".join(f"{k[:-3]}={v}ms" for k, v in t.items() if k.endswith("_ms"))
            logger.info("[%-9s] %s%s", role, text,
                        f"\n            {bits}{extra}" if bits else "")
            asyncio.create_task(store.log_turn(
                call_id, seq, "agent" if role == "assistant" else "user", text,
                **{k: v for k, v in t.items() if k.endswith("_ms")}))
        except Exception:
            logger.exception("transcript handler failed")

    async def _shutdown():
        try:
            logger.info("usage: %s  kb_tool_calls=%d  transferred=%s",
                        usage.get_summary(), agent.tool_calls, agent.transferred)
            if agent.transferred:
                dest, reason = agent.transferred
                await store.end_call(call_id, "transferred", outcome=reason)
                await (await store.pool()).execute(
                    "UPDATE calls SET transferred_to=$2, transfer_reason=$3 WHERE id=$1",
                    call_id, dest, reason)
            else:
                await store.end_call(call_id, "completed")
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
        _kw["num_idle_processes"] = int(os.getenv("NUM_IDLE_PROCESSES", "3"))
    if "drain_timeout" in _p:
        _kw["drain_timeout"] = int(os.getenv("DRAIN_TIMEOUT", "150"))
    cli.run_app(WorkerOptions(**_kw))
