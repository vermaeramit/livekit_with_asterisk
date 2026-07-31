"""
Sarvam STT -> OpenAI LLM -> Sarvam TTS, with Silero VAD + multilingual turn detection.

Config comes from Postgres so the admin UI (Step 11) can change prompt/voice/model
without touching this file. Tuning knobs are env vars so latency can be iterated on
without editing code.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from livekit.agents import (
    Agent, AgentSession, JobContext, JobProcess,
    RoomInputOptions, WorkerOptions, cli, metrics,
)
from livekit.plugins import openai, sarvam, silero

# NOTE: livekit.agents.inference.TurnDetector is the newer API, but its signature
# (base_url/api_key/conn_options) shows it can call a remote gateway. Turn detection
# runs on EVERY turn - a network hop there would wreck the endpointing budget.
# MultilingualModel is confirmed local (onnxruntime). Deprecated but pinned.
import warnings
warnings.filterwarnings("ignore", message=".*turn_detector.*deprecated.*")
from livekit.plugins.turn_detector.multilingual import MultilingualModel  # noqa: E402

logger = logging.getLogger("voice-agent")

CONFIG_NAME = os.getenv("AGENT_CONFIG", "default")
# Biggest latency lever: how long to wait before deciding the caller stopped.
MIN_ENDPOINTING = float(os.getenv("MIN_ENDPOINTING_DELAY", "0.25"))
# Cap on how long the turn detector may stall when it is unsure. At the default
# 4.0s, short closings like "ओके थैंक यू" scored below threshold and froze the
# call for the full 4 seconds. 1.5s bounds that.
MAX_ENDPOINTING = float(os.getenv("MAX_ENDPOINTING_DELAY", "1.5"))


def prewarm(proc: JobProcess):
    """Load VAD before any job arrives.

    Only VAD goes here. MultilingualModel needs a job context (it talks to the
    inference executor process), so instantiating it in prewarm crashes the worker.
    That executor is pre-warmed separately at worker startup anyway.
    """
    t = time.perf_counter()
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("prewarm (VAD) complete in %.0f ms", (time.perf_counter() - t) * 1000)


def _stt_kwargs(cfg):
    """Sarvam STT options, tunable from env.

    Sarvam's SERVER-side VAD decides END_SPEECH; only then does the plugin send a
    flush and the transcript comes back ~120ms later. Measured ~700ms between our
    local Silero saying "user stopped" and Sarvam agreeing. The plugin has no hook
    for the local VAD to force a flush, so these params are the only lever.

    Caveat: MODEL_CONFIGS['saarika:*'].supports_vad_params is False, so the
    fine-grained knobs are silently DROPPED for saarika. Only high_vad_sensitivity
    and flush_signal survive. saaras:v3 has supports_vad_params=True.
    """
    model = os.getenv("SARVAM_STT_MODEL") or cfg.stt_model or "saarika:v2.5"
    kw = {"language": cfg.language, "model": model}

    if os.getenv("SARVAM_STT_MODE"):
        kw["mode"] = os.getenv("SARVAM_STT_MODE")

    # not gated by supports_vad_params - work on every model
    if os.getenv("SARVAM_HIGH_VAD"):
        kw["high_vad_sensitivity"] = os.getenv("SARVAM_HIGH_VAD") == "1"
    if os.getenv("SARVAM_FLUSH_SIGNAL"):
        kw["flush_signal"] = os.getenv("SARVAM_FLUSH_SIGNAL") == "1"

    # gated by supports_vad_params - saaras:v3 only
    for env, key, cast in (
        ("SARVAM_NEG_FRAMES", "negative_frames_count", int),
        ("SARVAM_NEG_WINDOW", "negative_frames_window", int),
        ("SARVAM_NEG_THRESH", "negative_speech_threshold", float),
        ("SARVAM_POS_THRESH", "positive_speech_threshold", float),
        ("SARVAM_MIN_SPEECH", "min_speech_frames", int),
    ):
        v = os.getenv(env)
        if v:
            kw[key] = cast(v)
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


async def entrypoint(ctx: JobContext):
    import store

    cfg = await store.load_config(CONFIG_NAME)
    stt_kw, tts_kw = _stt_kwargs(cfg), _tts_kwargs(cfg)
    logger.info("config=%s lang=%s stt=%s llm=%s tts=%s",
                cfg.name, cfg.language, stt_kw, cfg.llm_model, tts_kw)

    await ctx.connect()

    caller = callee = None
    for p in ctx.room.remote_participants.values():
        caller = caller or _sip_attr(p, "sip.phoneNumber", "sip.from_user")
        callee = callee or _sip_attr(p, "sip.trunkPhoneNumber", "sip.to_user")

    call_id = await store.start_call(ctx.room.name, caller, callee, cfg.name, cfg.language)
    logger.info("call_id=%s caller=%s callee=%s", call_id, caller, callee)

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
            name = type(m).__name__
            if name == "EOUMetrics":
                pending["eou_ms"] = int(m.end_of_utterance_delay * 1000)
                pending["stt_ms"] = int(m.transcription_delay * 1000)
            elif name == "LLMMetrics":
                pending["llm_ttft_ms"] = int(m.ttft * 1000)
            elif name == "TTSMetrics":
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
            if t:
                # eou ALREADY includes stt (transcription_delay); summing all four
                # double-counts. Time-to-first-audio = eou + llm_ttft + tts_ttfb.
                t["total_ms"] = (t.get("eou_ms", 0) + t.get("llm_ttft_ms", 0)
                                 + t.get("tts_ttfb_ms", 0))
                pending.clear()

            bits = "  ".join(f"{k[:-3]}={v}ms" for k, v in t.items())
            logger.info("[%-9s] %s%s", role, text, f"\n            {bits}" if bits else "")
            asyncio.create_task(store.log_turn(
                call_id, seq, "agent" if role == "assistant" else "user", text, **t))
        except Exception:
            logger.exception("transcript handler failed")

    async def _shutdown():
        try:
            logger.info("usage: %s", usage.get_summary())
            await store.end_call(call_id, "completed")
            await store.close()
        except Exception:
            logger.exception("shutdown failed")

    ctx.add_shutdown_callback(_shutdown)

    await session.start(room=ctx.room, agent=Agent(instructions=cfg.instructions),
                        room_input_options=RoomInputOptions())

    if cfg.greeting:
        await session.say(cfg.greeting, allow_interruptions=cfg.allow_interrupt)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))
