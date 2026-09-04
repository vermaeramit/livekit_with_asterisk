"""Talk to a campaign's agent without dialling it.

Every change to a prompt, a knowledge base or a tool has needed a real phone
call to see the effect. A call takes two minutes, and it tells you what the
agent said without telling you why it said it.

This runs the campaign's own configuration through the agent's own code -
`prompt.build_instructions`, `kb.search`, `tools.build_raw` - and returns the
answer together with the working: which documents were retrieved and at what
score, which tools ran with what arguments, and what the turn cost.

Nothing is stored. A test conversation should not appear in the call list
beside real ones, and the history lives in the browser.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from .. import db, kblib
from .. import provider_keys as pk
from ..deps import CurrentUser, assert_campaign_visible, require_perm
from ..schemas import ChatTurnIn

log = logging.getLogger("admin-api")
router = APIRouter(tags=["chat"])

# campaign.write, not calls.read: a turn spends the campaign's OpenAI budget
# and can call its tools for real, which may be a live dealer lookup.
editor = require_perm("campaign.write")


@router.post("/campaigns/{campaign_id}/chat")
async def chat_turn(campaign_id: int, body: ChatTurnIn,
                    actor: CurrentUser = Depends(editor)):
    if not kblib.available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"the agent library is not loaded: {kblib.why_unavailable()}")

    tenant_id = await assert_campaign_visible(actor, campaign_id)

    keys = await pk.resolve(tenant_id=tenant_id, campaign_id=campaign_id)
    if not keys.get("openai"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this campaign has no OpenAI key - a test turn is a real request, "
            "billed to it exactly as a call is")

    # The agent's own loader, so the config object is the one the call gets -
    # including the JSONB columns it decodes and the fields a dataclass would
    # otherwise silently drop.
    store = kblib.agent_module("store")
    chat = kblib.agent_module("chat")

    cfg_row = await db.pool().fetchrow(
        "SELECT name FROM agent_config WHERE campaign_id = $1 ORDER BY id LIMIT 1",
        campaign_id)
    if cfg_row is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this campaign has no agent config")

    cfg = await store.load_config(cfg_row["name"])
    tool_specs = await store.load_tools(campaign_id)

    history = [{"role": m.role, "content": m.content} for m in body.history]
    history.append({"role": "user", "content": body.message})

    async def gen():
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def on_event(**event):
            await queue.put(event)

        task = asyncio.create_task(
            chat.reply(cfg, history, keys["openai"], tool_specs, on_event))

        try:
            # The queue drains as the model produces, so words appear while it
            # is still thinking. Waiting on it with a timeout rather than on
            # the task keeps a long tool call from starving the connection.
            while not task.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    event = {"stage": "working"}
                yield json.dumps(event) + "\n"
            out = await task
        except Exception as e:
            log.exception("chat turn failed for campaign %s", campaign_id)
            yield json.dumps({"stage": "error",
                              "message": f"{type(e).__name__}: {e}"}) + "\n"
            return

        yield json.dumps({
            "stage": "done",
            "text": out.text,
            "steps": [dataclasses.asdict(s) for s in out.steps],
            "prompt_tokens": out.prompt_tokens,
            "completion_tokens": out.completion_tokens,
            "cached_tokens": out.cached_tokens,
            "first_token_ms": out.first_token_ms,
            "ms": out.ms,
        }) + "\n"

    return StreamingResponse(
        gen(), media_type="application/x-ndjson",
        # nginx buffers proxied responses by default, which would hold every
        # word back until the end and defeat the whole point.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})
