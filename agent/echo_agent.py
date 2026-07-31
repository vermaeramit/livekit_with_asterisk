"""
Step 7 - transport latency baseline. No AI in this path.

Joins each SIP call room, plays a beep on connect, then echoes the caller's
audio straight back. Measures how much latency the agent itself adds.
"""
import asyncio
import logging
import time

import os
import numpy as np
from livekit import rtc
from livekit.agents import JobContext, WorkerOptions, cli

logger = logging.getLogger("echo-agent")

SAMPLE_RATE = 48000
NUM_CHANNELS = 1
FRAME_MS = 10
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

async def play_beep(source: rtc.AudioSource, freq=880, secs=0.25):
    """Short tone so you instantly know the downlink works."""
    n = int(SAMPLE_RATE * secs)
    t = np.arange(n) / SAMPLE_RATE
    pcm = (np.sin(2 * np.pi * freq * t) * 0.25 * 32767).astype(np.int16)
    for i in range(0, n, FRAME_SAMPLES):
        chunk = pcm[i:i + FRAME_SAMPLES]
        if len(chunk) < FRAME_SAMPLES:
            chunk = np.pad(chunk, (0, FRAME_SAMPLES - len(chunk)))
        await source.capture_frame(rtc.AudioFrame(
            data=chunk.tobytes(),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            samples_per_channel=FRAME_SAMPLES,
        ))

async def echo_loop(track: rtc.Track, source: rtc.AudioSource):
    stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=NUM_CHANNELS)
    n = 0
    worst = 0.0
    total = 0.0
    t_first = None

    async for ev in stream:
        t0 = time.perf_counter()
        await source.capture_frame(ev.frame)
        dt = (time.perf_counter() - t0) * 1000

        if t_first is None:
            t_first = t0
            logger.info("FIRST AUDIO FRAME RECEIVED - echo loop live")

        n += 1
        total += dt
        worst = max(worst, dt)
        if n % 500 == 0:  # every ~5s
            logger.info(
                "frames=%d  agent_hop avg=%.3fms  max=%.3fms", n, total / n, worst
            )

async def entrypoint(ctx: JobContext):
    t_start = time.perf_counter()
    await ctx.connect()
    logger.info("CONNECTED room=%s in %.0fms",
                ctx.room.name, (time.perf_counter() - t_start) * 1000)

    # queue_size_ms small on purpose: the default 1000ms would hide latency here.
    # NOTE for Step 8 - this exact queue is what barge-in must flush.
    QUEUE_MS = int(os.getenv("ECHO_QUEUE_MS", "60"))
    source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS, queue_size_ms=QUEUE_MS)
    track = rtc.LocalAudioTrack.create_audio_track("echo", source)
    await ctx.room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    logger.info("published echo track")

    logger.info("queue_size_ms=%d  beep=%s", QUEUE_MS, os.getenv("ECHO_BEEP", "1"))
    if os.getenv("ECHO_BEEP", "1") == "1":
        await asyncio.sleep(0.4)
        await play_beep(source)
        logger.info("beep sent")

    started = set()

    def start_echo(t: rtc.Track, who: str):
        if t.sid in started or t.kind != rtc.TrackKind.KIND_AUDIO:
            return
        started.add(t.sid)
        logger.info("subscribing to audio from %s", who)
        asyncio.create_task(echo_loop(t, source))

    @ctx.room.on("track_subscribed")
    def _(t: rtc.Track, pub, participant):
        start_echo(t, participant.identity)

    # participant may already be in the room before the agent joined
    for p in ctx.room.remote_participants.values():
        for pub in p.track_publications.values():
            if pub.track:
                start_echo(pub.track, p.identity)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
