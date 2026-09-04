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

from .. import audit, db, kblib
from .. import provider_keys as pk
from ..deps import CurrentUser, assert_campaign_visible, require_perm
from . import widget as widget_mod
from ..schemas import ChatTurnIn, WidgetIn, WidgetOut

log = logging.getLogger("admin-api")
router = APIRouter(tags=["chat"])

# campaign.write, not calls.read: a turn spends the campaign's OpenAI budget
# and can call its tools for real, which may be a live dealer lookup.
editor = require_perm("campaign.write")


_WIDGET = """
    SELECT w.id, w.campaign_id, w.public_key, w.allowed_origins,
           w.allow_any_origin, w.enabled,
           w.daily_token_cap, w.welcome, w.title, w.accent_color,
           w.icon_url, w.created_at, w.updated_at,
           (SELECT coalesce(sum(prompt_tokens + completion_tokens), 0)
              FROM chat_conversations c
             WHERE c.widget_id = w.id
               AND c.last_at > now() - interval '24 hours') AS tokens_today,
           (SELECT count(*) FROM chat_conversations c
             WHERE c.widget_id = w.id
               AND c.started_at > now() - interval '24 hours') AS conversations_today
      FROM chat_widgets w
"""


@router.get("/campaigns/{campaign_id}/widget", response_model=WidgetOut | None)
async def get_widget(campaign_id: int, actor: CurrentUser = Depends(editor)):
    await assert_campaign_visible(actor, campaign_id)
    row = await db.pool().fetchrow(_WIDGET + " WHERE w.campaign_id = $1",
                                   campaign_id)
    return WidgetOut(**dict(row)) if row else None


@router.put("/campaigns/{campaign_id}/widget", response_model=WidgetOut)
async def save_widget(campaign_id: int, body: WidgetIn,
                      actor: CurrentUser = Depends(editor)):
    """Create the widget or change it. The key is issued once and kept.

    Kept because it is pasted into somebody's website: reissuing it on every
    save would silently break a live page, and the person saving would have no
    reason to expect it.
    """
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    existing = await db.pool().fetchrow(
        "SELECT public_key FROM chat_widgets WHERE campaign_id = $1", campaign_id)

    await db.pool().execute(
        """INSERT INTO chat_widgets (campaign_id, tenant_id, public_key,
                                     allowed_origins, allow_any_origin,
                                     enabled, daily_token_cap, welcome, title,
                                     accent_color, icon_url)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
           ON CONFLICT (campaign_id) DO UPDATE SET
               allowed_origins = EXCLUDED.allowed_origins,
               allow_any_origin = EXCLUDED.allow_any_origin,
               enabled = EXCLUDED.enabled,
               daily_token_cap = EXCLUDED.daily_token_cap,
               welcome = EXCLUDED.welcome,
               title = EXCLUDED.title,
               accent_color = EXCLUDED.accent_color,
               icon_url = EXCLUDED.icon_url,
               updated_at = now()""",
        campaign_id, tenant_id,
        existing["public_key"] if existing else widget_mod.new_key(),
        body.allowed_origins, body.allow_any_origin, body.enabled,
        body.daily_token_cap, body.welcome, body.title,
        body.accent_color, body.icon_url)

    await audit.record(actor, entity="chat_widget", entity_id=str(campaign_id),
                       action="update" if existing else "create",
                       tenant_id=tenant_id, campaign_id=campaign_id,
                       changes={"origins": {"from": None,
                                            "to": body.allowed_origins}})
    row = await db.pool().fetchrow(_WIDGET + " WHERE w.campaign_id = $1",
                                   campaign_id)
    return WidgetOut(**dict(row))


@router.delete("/campaigns/{campaign_id}/widget",
               status_code=status.HTTP_204_NO_CONTENT)
async def delete_widget(campaign_id: int, actor: CurrentUser = Depends(editor)):
    """Removes the widget and every conversation it held."""
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    await db.pool().execute("DELETE FROM chat_widgets WHERE campaign_id = $1",
                            campaign_id)
    await audit.record(actor, entity="chat_widget", entity_id=str(campaign_id),
                       action="delete", tenant_id=tenant_id,
                       campaign_id=campaign_id)


@router.get("/campaigns/{campaign_id}/chat/opening")
async def chat_opening(campaign_id: int,
                       actor: CurrentUser = Depends(editor)):
    """The greeting, rendered the way a call renders it.

    Its own request rather than a field on the config the page already has:
    the placeholder substitution has rules about defaults after the pipe, and
    a copy of those in the browser would be a copy to get wrong.
    """
    await assert_campaign_visible(actor, campaign_id)
    if not kblib.available():
        return {"greeting": ""}

    store = kblib.agent_module("store")
    chat = kblib.agent_module("chat")
    row = await db.pool().fetchrow(
        "SELECT name FROM agent_config WHERE campaign_id = $1 ORDER BY id LIMIT 1",
        campaign_id)
    if row is None:
        return {"greeting": ""}
    cfg = await store.load_config(row["name"])
    return {"greeting": chat.opening(cfg)}


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
