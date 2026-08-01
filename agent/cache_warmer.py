"""Keep OpenAI's prompt cache warm so no caller pays for a cold first turn.

Measured on this deployment:
    cold          ttft 1198 ms   cached 0
    +2s           ttft  805 ms   cached 1152 / 1343
    +180s         ttft  950 ms   cached 1152   <- survives 3 minutes idle

Every call's first answer was landing at 1560-1926 ms - the one turn that sets
the caller's impression. The cache is org-level and keyed on the prompt prefix,
so ONE warmer keeps it hot for every concurrent call; per-call warming is not
needed.

Cost is a ~1300-token mostly-cached prompt and a single output token every
INTERVAL seconds.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from openai import AsyncOpenAI

import prompt as prompt_mod
import store

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cache-warmer")

INTERVAL = int(os.getenv("CACHE_WARM_INTERVAL", "120"))
RELOAD_EVERY = int(os.getenv("CACHE_WARM_RELOAD_EVERY", "10"))
CONFIG_NAME = os.getenv("AGENT_CONFIG", "default")


async def warm(client, model, instructions) -> tuple[float | None, int, int]:
    t0 = time.perf_counter()
    ttft = None
    usage = None
    stream = await client.chat.completions.create(
        model=model, temperature=0, stream=True,
        stream_options={"include_usage": True},
        max_completion_tokens=1,
        prompt_cache_key=CONFIG_NAME,
        messages=[{"role": "system", "content": instructions},
                  {"role": "user", "content": "ping"}])
    async for ch in stream:
        if ttft is None and ch.choices and ch.choices[0].delta.content:
            ttft = (time.perf_counter() - t0) * 1000
        if getattr(ch, "usage", None):
            usage = ch.usage
    cached = 0
    total = 0
    if usage:
        total = usage.prompt_tokens or 0
        d = getattr(usage, "prompt_tokens_details", None)
        cached = (d.cached_tokens or 0) if d else 0
    return ttft, total, cached


async def main():
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    cfg = await store.load_config(CONFIG_NAME)
    instructions, mode, ntok = await prompt_mod.build_instructions(cfg)
    log.info("warming config=%s model=%s kb=%s(%d tok) every %ds",
             cfg.name, cfg.llm_model, mode, ntok, INTERVAL)

    n = 0
    while True:
        try:
            # Config can change from the admin UI mid-flight. Reloading keeps the
            # warmed prefix matching what the agent actually sends - a stale
            # prefix warms a cache nobody uses.
            if n and n % RELOAD_EVERY == 0:
                cfg = await store.load_config(CONFIG_NAME)
                new_instr, mode, ntok = await prompt_mod.build_instructions(cfg)
                if new_instr != instructions:
                    log.info("prompt changed (%d -> %d chars) - rewarming",
                             len(instructions), len(new_instr))
                    instructions = new_instr

            ttft, total, cached = await warm(client, cfg.llm_model, instructions)
            pct = (cached / total * 100) if total else 0
            log.info("warm #%d  ttft=%sms  prompt=%d  cached=%d (%.0f%%)",
                     n, f"{ttft:.0f}" if ttft else "-", total, cached, pct)
        except Exception:
            log.exception("warm failed - retrying next cycle")
        n += 1
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
