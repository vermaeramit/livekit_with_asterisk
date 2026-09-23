"""Time a campaign's language model against others, on its own prompt.

    ( set -a; . /opt/aivoice/.env; set +a
      cd /srv/aivoice && /opt/aivoice/agent/.venv/bin/python server-configs/llm-bench.py default )

Built 23 Sep 2026, after the Soniox India region took TTS out of the picture.
On call 646 the caller waited 1308-2299 ms for an ordinary answer, and the
parts were: turn detection 353 ms, **the model 678-1600 ms**, the voice 190 ms.
The model is now two thirds of the wait, and the cheap fixes are already in -
the prompt cache is hitting (1152 of 1353 tokens on a mid-call turn), the
prompt is small, and preemptive generation is on. What is left is the model
itself.

Which is exactly the decision that must not be made from a table on a vendor's
website. gpt-4.1-mini was chosen here for VARIANCE, not average: it cut the
spread from 800 ms to 85 ms, and one 6286 ms turn had already ended a call.
A model that is 300 ms faster on average and occasionally three seconds slow is
worse, and only a measurement says which is which.

So: the campaign's real instructions, its real tools, a real mid-call question,
and every model asked the same thing the same number of times.

    cold    the first call, with nothing in the provider's prompt cache
    warm    the rest - which is what a call after its first turn actually gets
    spread  slowest warm minus fastest warm, the number that ended that call

Prompt caching is reproduced, not skipped: OpenAI's prompt_cache_key is sent
exactly as the agent sends it, because 90.8% of this system's prompt tokens are
served from cache and a bench without it measures a case that never happens.

Never prints a key.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time

# Beside this file when it is run from the checkout, and the checkout itself
# when it is not - same as tts-bench.py, for a box whose checkout is older.
for _agent_dir in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"),
                   "/srv/aivoice/agent"):
    if os.path.isfile(os.path.join(_agent_dir, "store.py")):
        sys.path.insert(0, _agent_dir)
        break

import prompt as prompt_mod                                      # noqa: E402
import providers as providers_mod                                # noqa: E402
import store                                                     # noqa: E402
import tools as tools_mod                                        # noqa: E402
from openai import AsyncOpenAI                                   # noqa: E402

# A mid-call exchange, not a greeting: the first turn of a call pays for a cold
# cache and is not what most turns look like. Two turns of history so the
# prompt has something in front of the question, as it does on a real call.
HISTORY = [
    {"role": "user", "content": "नमस्ते"},
    {"role": "assistant",
     "content": "नमस्ते! मैं आपकी किस तरह मदद कर सकता हूँ?"},
    {"role": "user",
     "content": "मुझे इसके बारे में थोड़ा और बताइए, और कीमत भी बता दीजिए"},
]


async def one(client: AsyncOpenAI, model: str, cfg, messages: list[dict],
              schemas: list[dict], cache_key: str | None) -> tuple[int, int, int, int]:
    """-> (ttft ms, total ms, prompt tokens, cached tokens).

    Streamed, because the first token is what a caller waits for and the last
    one is not. The stream is read to the end anyway: the usage block only
    arrives there, and a bench that reports no tokens cannot tell a cache hit
    from a cache miss.
    """
    kw = {}
    if cache_key:
        kw["prompt_cache_key"] = cache_key
    t0 = time.monotonic()
    first = None
    prompt_tokens = cached = 0
    stream = await client.chat.completions.create(
        model=model, temperature=cfg.llm_temperature, messages=messages,
        tools=schemas or None, stream=True,
        stream_options={"include_usage": True}, **kw)
    async for chunk in stream:
        if chunk.usage:
            prompt_tokens = chunk.usage.prompt_tokens or 0
            details = getattr(chunk.usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) or 0
        if first is None and chunk.choices:
            d = chunk.choices[0].delta
            # A tool call counts: the model has started answering, and on a
            # call the tool round trip is part of the same wait.
            if (d.content or "").strip() or getattr(d, "tool_calls", None):
                first = time.monotonic() - t0
    total = time.monotonic() - t0
    return int((first or total) * 1000), int(total * 1000), prompt_tokens, cached


async def bench(model: str, cfg, keys: dict, provider: str, runs: int,
                messages: list[dict], schemas: list[dict]) -> None:
    key = keys.get(provider)
    if not key:
        print(f"{model:<28} no {provider} key on this campaign - skipped")
        return

    base_url = providers_mod.llm_base_url(provider)
    # prompt_cache_key is OpenAI's own and means nothing to a gateway - the
    # agent makes the same distinction in _build_llm.
    cache_key = None if base_url else cfg.name
    client = AsyncOpenAI(api_key=key, **({"base_url": base_url} if base_url else {}))

    rows = []
    try:
        for i in range(runs + 1):
            r = await one(client, model, cfg, messages, schemas, cache_key)
            rows.append(r)
            # Not a rate limit dodge: back to back calls are not what a call
            # does, and OpenAI's cache is happier with a gap than without one.
            await asyncio.sleep(0.5)
    except Exception as e:
        # The provider's words, never the key.
        print(f"{model:<28} FAILED {type(e).__name__}: {str(e)[:110]}")
        return
    finally:
        await client.close()

    cold, warm = rows[0], rows[1:]
    ttfts = sorted(r[0] for r in warm)
    print(f"{model:<28} cold {cold[0]:>5}ms   warm p50 {int(statistics.median(ttfts)):>5}ms  "
          f"min {ttfts[0]:>5}  max {ttfts[-1]:>5}  spread {ttfts[-1] - ttfts[0]:>5}ms   "
          f"prompt {warm[-1][2]:>5}tok  cached {warm[-1][3]:>5}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("campaign", help="config name, e.g. default")
    ap.add_argument("--models", default="",
                    help="comma separated; default is the campaign's own model "
                         "plus the usual alternatives")
    ap.add_argument("--provider", default="",
                    help="openai or openrouter; default is the campaign's own")
    ap.add_argument("--runs", type=int, default=5,
                    help="warm runs per model, after one cold run")
    ap.add_argument("--no-tools", action="store_true",
                    help="leave the campaign's tools out - they cost prompt "
                         "tokens, so this is a different question")
    args = ap.parse_args()

    cfg = await store.load_config(args.campaign)
    keys = await store.load_provider_keys(cfg.campaign_id)
    provider = args.provider or cfg.llm_provider or "openai"

    instructions, kb_mode, kb_tokens = await prompt_mod.build_instructions(cfg)
    messages = [{"role": "system", "content": instructions}] + HISTORY

    schemas: list[dict] = []
    if not args.no_tools:
        async def _record(**_):
            return None
        for spec in await store.load_tools(cfg.campaign_id):
            try:
                name, schema, _run = tools_mod.build_raw(spec, None, _record)
            except Exception as e:
                print(f"tool {spec.get('name')!r} skipped: {type(e).__name__}")
                continue
            schemas.append({"type": "function",
                            "function": {"name": name, **schema}})
        if cfg.kb_enabled:
            # The same declaration the text tester sends, taken from there
            # rather than written again: a second copy would drift, and its
            # tokens are part of what the model is being timed on.
            from chat import _KB_TOOL
            schemas.append(_KB_TOOL)

    models = ([m.strip() for m in args.models.split(",") if m.strip()]
              or [cfg.llm_model, "gpt-4.1-nano", "gpt-4o-mini"])

    print(f"campaign {cfg.name}: on {provider}/{cfg.llm_model}, "
          f"temperature {cfg.llm_temperature}")
    print(f"prompt {len(instructions)} chars, kb {kb_mode} ({kb_tokens} tok), "
          f"{len(schemas)} tools, {args.runs} warm runs each\n")
    for model in models:
        await bench(model, cfg, keys, provider, args.runs, messages, schemas)


if __name__ == "__main__":
    asyncio.run(main())
