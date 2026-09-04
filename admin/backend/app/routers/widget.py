"""The public end of the chat widget.

Unauthenticated by design: this is called by a script tag on somebody's public
website, and there is nowhere to keep a secret in a page anyone can view.

So the key identifies, it does not authorise, and three things do the work
instead:

  the Origin header   a browser sets it and a browser cannot forge it. An
                      empty allowlist means the widget is off - fail closed,
                      because fail-open means a stranger's site running this
                      bot on this campaign's bill.

                      Unless allow_any_origin is set, which is a supported
                      choice and not a loophole: some customers have dozens of
                      subdomains, and the alternative to offering this is
                      somebody putting the key somewhere worse. With it on,
                      the cap below is the only limit left.
  a daily token cap   the ceiling on what one day can cost, counted in tokens
                      rather than rupees. A rupee cap needs a complete rate
                      table; the dashboard says five providers have none.
  a per-session limit the same visitor cannot hold a hundred turns open.

None of them stops somebody determined with curl and a forged Origin. They stop
the ordinary ways this goes wrong - a copied snippet, a scraper, a loop - and
the cap stops all of them from mattering.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from .. import db, kblib
from .. import provider_keys as pk
from ..schemas import WidgetTurnIn

log = logging.getLogger("admin-api")
router = APIRouter(tags=["widget"])

# A visitor's whole conversation. Long enough for a real question and its
# follow-ups, short enough that nobody uses this as free ChatGPT.
MAX_TURNS = 30


async def _widget(public_key: str, origin: str | None):
    row = await db.pool().fetchrow(
        """SELECT w.id, w.campaign_id, w.tenant_id, w.allowed_origins,
                  w.allow_any_origin,
                  w.enabled, w.daily_token_cap, w.welcome, w.title,
                  w.accent_color, (w.icon_data IS NOT NULL) AS has_icon,
                  ac.name AS config_name
             FROM chat_widgets w
             LEFT JOIN agent_config ac ON ac.campaign_id = w.campaign_id
            WHERE w.public_key = $1""", public_key)
    # One message for "no such key" and for "wrong origin". Telling a caller
    # which of the two it was is telling them how to get closer.
    if row is None or not row["enabled"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no widget here")

    if not row["allow_any_origin"]:
        allowed = list(row["allowed_origins"] or ())
        if not allowed or origin not in allowed:
            log.warning("widget %s refused origin %r", public_key, origin)
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "this widget is not enabled for that site")
    return row


def _cors(origin: str) -> dict:
    return {
        # The caller's own origin, or "*" when there is none - a request with
        # no Origin is not a browser, and only reaches here on a widget that
        # has been opened to everyone.
        "Access-Control-Allow-Origin": origin or "*",
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        # Ten minutes: long enough to save the preflight on a real
        # conversation, short enough that removing an origin takes effect
        # while somebody is still watching.
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


@router.get("/widget/{public_key}/config")
async def widget_config(public_key: str, request: Request):
    """What the script needs before anyone types: the title and the opening.

    Also the first origin check, so a widget on the wrong site fails while it
    is being installed rather than on a visitor's first question.
    """
    origin = request.headers.get("origin")
    w = await _widget(public_key, origin)
    return Response(
        content=json.dumps({
            "title": w["title"] or "Chat",
            "welcome": w["welcome"] or "Hello. How can I help?",
            "accent": w["accent_color"] or "#2563eb",
            # A PATH, not a URL. The widget prefixes it with the address it
            # loaded itself from, which is the only one it can be sure of -
            # building an absolute URL here would mean trusting proxy headers
            # to say what the outside world calls this service.
            "icon": (f"/widget/{public_key}/icon" if w["has_icon"] else None),
        }),
        media_type="application/json",
        headers=_cors(origin or ""))


@router.get("/widget/{public_key}/icon")
async def widget_icon(public_key: str):
    """The uploaded logo.

    No origin check: an <img> is not a CORS request, and a logo is not a
    secret. Checking would only stop the image loading on the very site the
    widget is meant for, since <img> sends no Origin at all.
    """
    row = await db.pool().fetchrow(
        "SELECT icon_data, icon_mime FROM chat_widgets "
        " WHERE public_key = $1 AND enabled", public_key)
    if row is None or row["icon_data"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no icon")
    return Response(
        content=bytes(row["icon_data"]),
        media_type=row["icon_mime"] or "image/png",
        headers={
            # A logo changes rarely and is fetched on every visitor's first
            # click. An hour is short enough that replacing it is visible the
            # same afternoon.
            "Cache-Control": "public, max-age=3600",
            # Belt and braces on top of refusing SVG at upload: even if
            # something image-shaped got through, the browser will not sniff
            # it into something executable.
            "X-Content-Type-Options": "nosniff",
        })


@router.post("/widget/{public_key}/chat")
async def widget_chat(public_key: str, body: WidgetTurnIn, request: Request):
    """One turn, streamed as newline-delimited JSON.

    Streamed because the visitor is watching a screen. On a phone call the
    agent's thinking time is filled by the line being open; here it is a blank
    panel, and eight seconds of that is a person deciding the thing is broken.
    """
    origin = request.headers.get("origin")
    w = await _widget(public_key, origin)
    headers = _cors(origin or "")

    def line(**payload) -> str:
        return json.dumps(payload) + "\n"

    def refuse(text: str):
        # 200 with a sentence, never a status code. The person reading it is a
        # customer of our customer: they did not choose this software and
        # cannot act on a 429.
        return Response(content=line(delta=text) + line(done=True),
                        media_type="application/x-ndjson", headers=headers)

    if not kblib.available() or w["config_name"] is None:
        return refuse("Sorry, chat is not available right now.")

    # The cap, before anything is spent. Counted from the conversations
    # themselves, so it is what was used and not what somebody estimated.
    used = await db.pool().fetchval(
        """SELECT coalesce(sum(prompt_tokens + completion_tokens), 0)
             FROM chat_conversations
            WHERE widget_id = $1 AND last_at > now() - interval '24 hours'""",
        w["id"]) or 0
    if used >= w["daily_token_cap"]:
        log.warning("widget %s hit its daily cap (%s tokens)", public_key, used)
        return refuse("Sorry, I am not available right now. Please try again later.")

    keys = await pk.resolve(tenant_id=w["tenant_id"], campaign_id=w["campaign_id"])
    if not keys.get("openai"):
        return refuse("Sorry, chat is not available right now.")

    store = kblib.agent_module("store")
    chat = kblib.agent_module("chat")
    cfg = await store.load_config(w["config_name"])
    tool_specs = await store.load_tools(w["campaign_id"])

    conv, history = await _conversation(w, body.session_id, origin)
    if len(history) >= MAX_TURNS * 2:
        return refuse("This conversation has gone on a long time. Please start a new one.")

    history.append({"role": "user", "content": body.message})

    async def gen():
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def on_event(**event):
            # Only the words. A visitor has no use for which document was
            # retrieved, and the scores are the campaign's business.
            if event.get("stage") == "delta":
                await queue.put(event)

        task = asyncio.create_task(
            chat.reply(cfg, history, keys["openai"], tool_specs, on_event))

        try:
            while not task.done() or not queue.empty():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    # Keeps the connection alive through a slow tool without
                    # putting anything on the visitor's screen.
                    yield line(ping=True)
                    continue
                yield line(delta=event["text"])
            out = await task
        except Exception:
            log.exception("widget turn failed for %s", public_key)
            yield line(delta="Sorry, something went wrong. Please try again.")
            yield line(done=True)
            return

        await _store_turn(conv, body.message, out)
        # The final text as well as the deltas: a dropped frame would otherwise
        # leave a sentence half-written, and this is a customer reading it.
        yield line(text=out.text, done=True)

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={**headers, "X-Accel-Buffering": "no",
                                      "Cache-Control": "no-store"})


async def _conversation(w, session_id: str, origin: str | None):
    """The row for this visitor, and what has been said so far.

    History comes from the DATABASE, not from the browser. A client that sends
    its own history can send any history - including one where the agent has
    already agreed to something.
    """
    conv = await db.pool().fetchrow(
        "SELECT id FROM chat_conversations WHERE session_id = $1", session_id)
    if conv is None:
        conv = await db.pool().fetchrow(
            """INSERT INTO chat_conversations
                   (widget_id, campaign_id, tenant_id, session_id, origin)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            w["id"], w["campaign_id"], w["tenant_id"], session_id, origin)
        return conv["id"], []

    rows = await db.pool().fetch(
        "SELECT role, content FROM chat_messages "
        " WHERE conversation_id = $1 ORDER BY id", conv["id"])
    return conv["id"], [{"role": r["role"], "content": r["content"]} for r in rows]


async def _store_turn(conv_id: int, question: str, out) -> None:
    async with db.pool().acquire() as c:
        async with c.transaction():
            await c.execute(
                "INSERT INTO chat_messages (conversation_id, role, content) "
                "VALUES ($1, 'user', $2)", conv_id, question)
            await c.execute(
                "INSERT INTO chat_messages (conversation_id, role, content, "
                "steps, ms) VALUES ($1, 'assistant', $2, $3::jsonb, $4)",
                conv_id, out.text,
                json.dumps([_step(s) for s in out.steps]), out.ms)
            await c.execute(
                """UPDATE chat_conversations
                      SET prompt_tokens = prompt_tokens + $2,
                          completion_tokens = completion_tokens + $3,
                          last_at = now()
                    WHERE id = $1""",
                conv_id, out.prompt_tokens, out.completion_tokens)


def _step(s) -> dict:
    return {"kind": s.kind, "name": s.name, "args": s.args,
            "ms": s.ms, "hits": s.hits}


def new_key() -> str:
    """Public, and long anyway.

    Length is not what protects this - the origin allowlist and the cap are -
    but a short key invites somebody to try the next one along, and the log
    fills up with refusals nobody reads.
    """
    return "wk_" + secrets.token_urlsafe(24)


# ─────────────────────────── the preflight ─────────────────────────────────

_WIDGET_PATH = re.compile(r"^/api/widget/([A-Za-z0-9_-]+)/")


async def preflight_middleware(request: Request, call_next):
    """Answer OPTIONS for widget routes before the global CORS middleware can.

    That middleware allows one fixed list of origins - the console's own - and
    it intercepts every preflight, so a widget on a customer's site got a
    response with no Access-Control-Allow-Origin at all and the browser refused
    the POST. The GET worked and hid it: a simple request has no preflight, and
    the header on that response is one this module sets itself.

    Whether a given site may use a given widget is a per-widget question, and
    the only place that can answer it is here. Added AFTER the CORS middleware
    in main.py, which makes it the outer one and lets it reply first.
    """
    if request.method != "OPTIONS":
        return await call_next(request)

    m = _WIDGET_PATH.match(request.url.path)
    if not m:
        return await call_next(request)

    origin = request.headers.get("origin")
    try:
        # Checked here too. Answering a preflight for an origin that will be
        # refused anyway tells that origin it is worth trying again.
        await _widget(m.group(1), origin)
    except HTTPException:
        # No CORS headers. The browser reports it as a refusal, which is what
        # it is - and the reason is in this service's log, not in a response
        # that anybody can read.
        return Response(status_code=403)
    return Response(status_code=204, headers=_cors(origin or ""))
