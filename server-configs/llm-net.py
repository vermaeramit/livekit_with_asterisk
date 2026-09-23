"""How much of the model's answer time is the network, and how much is the model.

    ( set -a; . /opt/aivoice/.env; set +a
      cd /srv/aivoice && /opt/aivoice/agent/.venv/bin/python server-configs/llm-net.py default )

Asked on 23 Sep 2026, after the Soniox India region cut the voice leg from
seconds to 190 ms and left the model as two thirds of a caller's wait. The
question is the same one that was right about Soniox: is the answer slow
because the work is slow, or because the work is far away?

Step 1 measured api.openai.com at a 7 ms TCP connect from .243 and recorded a
caveat with it - a 7 ms handshake reaches the nearest TLS edge, which says
nothing about where the model actually runs. This settles it, because OpenAI
answers with a header that says how long THEY took:

    openai-processing-ms    their side, from request received to response sent
    wall                    ours, from the first byte sent to the last received
    network                 wall - processing: TLS, both directions, everything
                            between this box and the machine that did the work

A minimal request is sent first - four tokens in, one out - because that
isolates the fixed cost. Whatever it takes, the campaign's real prompt pays it
too. Then the campaign's own prompt, so the two can be compared.

Not streamed, deliberately: the header only comes with a complete response, and
what is being measured here is distance, not time-to-first-token.

Never prints a key.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import ssl
import statistics
import sys
import time

for _agent_dir in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"),
                   "/srv/aivoice/agent"):
    if os.path.isfile(os.path.join(_agent_dir, "store.py")):
        sys.path.insert(0, _agent_dir)
        break

import prompt as prompt_mod                                      # noqa: E402
import providers as providers_mod                                # noqa: E402
import store                                                     # noqa: E402
from openai import AsyncOpenAI                                   # noqa: E402


def tcp_and_tls(host: str, port: int = 443) -> None:
    """Where the nearest edge is, and what it costs to reach it.

    Reported separately from the request below because they answer different
    questions: this is the distance to the door, that is the distance to the
    work. Soniox's door was 8 ms away and its work was in the United States.
    """
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except Exception as e:
        print(f"{host}: cannot resolve - {type(e).__name__}")
        return
    addrs = sorted({i[4][0] for i in infos})
    print(f"{host} -> {', '.join(addrs)}")

    ctx = ssl.create_default_context()
    connects, handshakes = [], []
    for _ in range(3):
        t0 = time.monotonic()
        s = socket.create_connection((addrs[0], port), timeout=10)
        t1 = time.monotonic()
        with ctx.wrap_socket(s, server_hostname=host):
            t2 = time.monotonic()
        connects.append((t1 - t0) * 1000)
        handshakes.append((t2 - t1) * 1000)
    print(f"  tcp connect {statistics.median(connects):6.1f} ms     "
          f"tls handshake {statistics.median(handshakes):6.1f} ms")


async def ask(client: AsyncOpenAI, model: str, messages: list[dict],
              max_out: int, label: str, runs: int) -> None:
    rows = []
    for _ in range(runs):
        t0 = time.monotonic()
        try:
            raw = await client.chat.completions.with_raw_response.create(
                model=model, messages=messages, max_completion_tokens=max_out)
        except Exception as e:
            print(f"{label:<22} FAILED {type(e).__name__}: {str(e)[:100]}")
            return
        wall = (time.monotonic() - t0) * 1000
        # Their own number. Absent on a gateway, which is worth saying out loud
        # rather than reporting a zero that looks like an answer.
        theirs = raw.headers.get("openai-processing-ms")
        body = raw.parse()
        usage = getattr(body, "usage", None)
        rows.append((wall, float(theirs) if theirs else None,
                     getattr(usage, "prompt_tokens", 0) or 0))
        await asyncio.sleep(0.4)

    walls = sorted(r[0] for r in rows)
    theirs = [r[1] for r in rows if r[1] is not None]
    prompt_tokens = rows[-1][2]
    if not theirs:
        print(f"{label:<22} wall p50 {statistics.median(walls):6.0f} ms   "
              f"(no openai-processing-ms header - a gateway, so the split "
              f"cannot be made)")
        return
    net = statistics.median(walls) - statistics.median(theirs)
    print(f"{label:<22} wall p50 {statistics.median(walls):6.0f} ms   "
          f"their side {statistics.median(theirs):6.0f} ms   "
          f"network {net:6.0f} ms   prompt {prompt_tokens} tok")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("campaign", help="config name, e.g. default")
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    cfg = await store.load_config(args.campaign)
    keys = await store.load_provider_keys(cfg.campaign_id)
    provider = cfg.llm_provider or "openai"
    key = keys.get(provider)
    if not key:
        raise SystemExit(f"no {provider} key on campaign {cfg.name}")

    base_url = providers_mod.llm_base_url(provider)
    host = (base_url.split("/")[2] if base_url else "api.openai.com")
    print(f"campaign {cfg.name}: {provider}/{cfg.llm_model}\n")
    await asyncio.to_thread(tcp_and_tls, host)
    print()

    client = AsyncOpenAI(api_key=key, **({"base_url": base_url} if base_url else {}))
    try:
        # Four tokens in, one out. Whatever this costs is the floor under every
        # answer the campaign gives.
        await ask(client, cfg.llm_model, [{"role": "user", "content": "hi"}],
                  1, "smallest possible", args.runs)

        instructions, _, _ = await prompt_mod.build_instructions(cfg)
        real = [{"role": "system", "content": instructions},
                {"role": "user", "content": "कीमत क्या है?"}]
        await ask(client, cfg.llm_model, real, 1, "campaign prompt", args.runs)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
