"""Per-campaign HTTP tools.

A campaign can give the agent tools that call the client's own API mid-call -
look up a service history, check a warranty, book an appointment. They are
defined in the console (campaign_tools) and built here at job start.

Everything in this module exists because a tool call happens WHILE SOMEONE IS
LISTENING. A slow API is heard as silence; a large response is paid for in the
next prompt; a failed call must produce something the model can say out loud
rather than an exception that ends the conversation.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import socket
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import aiohttp
from livekit.agents import llm as lk_llm

import toolfmt

log = logging.getLogger("voice-agent")

# Off by default, by decision: clients host their APIs wherever they like and an
# allowlist was judged too much friction. Turn it on with
# TOOL_BLOCK_PRIVATE_HOSTS=1 and loopback, private ranges and the cloud metadata
# address stop being reachable.
#
# Worth knowing what "off" means: a tool URL is fetched BY THIS SERVER, from
# inside the network, and the model decides when. 169.254.169.254 and
# 127.0.0.1 have no legitimate use as a client API - they are only ever an
# attack path - so this is one env var away whenever that trade stops being
# worth it.
BLOCK_PRIVATE = os.getenv("TOOL_BLOCK_PRIVATE_HOSTS", "0") == "1"

USER_AGENT = os.getenv("TOOL_USER_AGENT", "AIVoice-Agent/1.0")

_session: aiohttp.ClientSession | None = None


def _http() -> aiohttp.ClientSession:
    """One session per job process.

    Not one per call: a fresh TCP (and TLS) handshake costs 100-200 ms, which is
    a tenth of the entire turn budget spent before the request even starts.
    """
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def aclose() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _host_is_private(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Cannot resolve - let the request itself fail and be recorded, rather
        # than guessing here.
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def _idempotency_key(call_id: int, name: str, args: dict[str, Any]) -> str:
    """Stable across a retry of the SAME call with the SAME arguments.

    A model asked twice for the same booking will send the same arguments, so
    this is what stops it becoming two appointments - IF the client's API
    honours the header. Nothing here can force that, which is why every
    invocation is also recorded.
    """
    blob = f"{call_id}|{name}|{json.dumps(args, sort_keys=True, default=str)}"
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def build(spec: dict, call_id: int | None, record: Callable):
    """-> a livekit function tool for one campaign_tools row.

    `record` is awaited with the outcome of every invocation. It is passed in
    rather than imported so this module does not depend on the database.
    """
    name: str = spec["name"]
    method: str = (spec.get("method") or "GET").upper()
    timeout = aiohttp.ClientTimeout(total=(spec.get("timeout_ms") or 2500) / 1000)
    max_bytes: int = spec.get("max_response_bytes") or 8192

    async def run(raw_arguments: dict[str, object]) -> str:
        args = dict(raw_arguments or {})
        t0 = time.perf_counter()
        url = toolfmt.fill(spec["url"], args) or ""

        async def done(status=None, err=None):
            # The resolved url, not the template. A placeholder that did not
            # substitute is invisible in the arguments - they are correct - and
            # only shows up here, in what was actually requested.
            await record(tool_id=spec.get("id"), name=name, arguments=args,
                         status_code=status, error=err, url=url,
                         duration_ms=int((time.perf_counter() - t0) * 1000))

        if BLOCK_PRIVATE and _host_is_private(url):
            await done(err="blocked: private address")
            log.warning("tool %s blocked: %s resolves to a private address", name, url)
            raise lk_llm.ToolError(
                "That lookup is not available. Tell the caller you cannot check "
                "it right now.")

        # A real User-Agent, not aiohttp's default. Cloudflare and most WAFs
        # answer "Python/3.12 aiohttp/..." with 403 and error code 1010 - which
        # is how this was found, against three separate public APIs that all
        # worked from curl on the same host. A client API behind a WAF would
        # have failed the same way, mid-call.
        # Overridable: spec headers are applied after this.
        headers = {"User-Agent": USER_AGENT}
        headers.update({k: str(v) for k, v in (spec.get("headers") or {}).items()})
        if spec.get("auth_header") and spec.get("auth_value"):
            headers[spec["auth_header"]] = spec["auth_value"]
        if method != "GET" and call_id is not None:
            headers["Idempotency-Key"] = _idempotency_key(call_id, name, args)

        data = None
        if method != "GET" and spec.get("body_template"):
            data = toolfmt.fill(spec["body_template"], args)
            headers.setdefault("Content-Type", "application/json")

        try:
            async with _http().request(method, url, headers=headers, data=data,
                                       timeout=timeout,
                                       allow_redirects=False) as r:
                # read() with a cap, not text(): an API that returns 50 MB
                # should cost us 8 KB and a truncation, not memory.
                raw = await r.content.read(max_bytes + 1)
                truncated = len(raw) > max_bytes
                text = raw[:max_bytes].decode("utf-8", "replace")
                status = r.status
        except asyncio.TimeoutError:
            await done(err="timeout")
            log.warning("tool %s timed out after %sms", name, spec.get("timeout_ms"))
            # ToolError, not an exception: the model needs something it can say.
            # Raising anything else here ends the turn and the caller hears
            # nothing at all.
            raise lk_llm.ToolError(
                "That system did not respond in time. Tell the caller you could "
                "not fetch it and offer to continue without it.")
        except Exception as e:
            await done(err=f"{type(e).__name__}: {e}"[:200])
            log.exception("tool %s failed", name)
            raise lk_llm.ToolError(
                "That lookup failed. Tell the caller you could not check it "
                "right now.")

        await done(status=status)

        if status >= 400:
            log.warning("tool %s -> HTTP %s", name, status)
            raise lk_llm.ToolError(
                f"The lookup returned an error ({status}). Tell the caller you "
                "could not retrieve it.")

        try:
            body = toolfmt.extract(json.loads(text), spec.get("response_path"))
            out = json.dumps(body, ensure_ascii=False, default=str)
        except (json.JSONDecodeError, TypeError):
            # Not JSON, or the path missed. The raw text is still the most
            # useful thing to hand the model.
            out = text

        log.info("  TOOL %s(%s) -> %s in %dms%s", name, args, status,
                 int((time.perf_counter() - t0) * 1000),
                 " [truncated]" if truncated else "")
        return out if out.strip() else "The lookup returned no data."

    return lk_llm.function_tool(
        run,
        raw_schema={
            "name": name,
            "description": spec["description"],
            "parameters": spec.get("parameters")
            or {"type": "object", "properties": {}},
        },
    )


def build_all(specs: list[dict], call_id: int | None, record: Callable) -> list:
    """One bad tool must not take the others down.

    A campaign with five tools and one malformed schema should lose one tool,
    not the whole call - and the log has to say which, because from the caller's
    side a missing tool is invisible.
    """
    out = []
    for spec in specs:
        try:
            out.append(build(spec, call_id, record))
        except Exception:
            log.exception("tool %r could not be built - skipping",
                          spec.get("name"))
    return out
