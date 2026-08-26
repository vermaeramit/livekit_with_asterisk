"""Turn a finished conversation into the fields the client's API wants.

Runs AFTER the call has ended, never during it. That is the whole design:

  * A tool the model calls mid-call would put an LLM round trip inside a turn
    budget that took a full day of measurement to get down to ~2.4 s. Nothing
    here is worth a second of a caller's time.
  * Doing it afterwards means the schema can change and old calls can be
    re-processed, which a mid-call tool can never offer.

What this module does NOT do is deliver. The job process exits when the call
ends, so it cannot retry anything; it writes a row and stops. admin-api sweeps
and delivers - see admin/backend/app/postback.py. One place owns delivery, and
it is the one that is still running a minute later.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("voice-agent")

# Only what a client API can sensibly receive. Anything richer belongs in a
# tool that runs during the call, where the model can be corrected.
_TYPES = {"string": "string", "number": "number", "boolean": "boolean"}


def build_schema(fields: list[dict]) -> dict | None:
    """The campaign's field list -> a JSON Schema for structured output.

    Every field is optional and nullable. A required field would make the model
    invent a value rather than admit the conversation never covered it, and an
    invented pincode is worse than a missing one.
    """
    props: dict[str, Any] = {}
    for f in fields or []:
        key = (f.get("key") or "").strip()
        if not key:
            continue
        kind = _TYPES.get((f.get("type") or "string").lower(), "string")
        props[key] = {
            "type": [kind, "null"],
            "description": (f.get("description") or "").strip() or key,
        }
    if not props:
        return None
    return {"type": "object", "properties": props,
            "required": list(props), "additionalProperties": False}


_SYSTEM = (
    "You read a finished phone conversation and record what it established.\n"
    "\n"
    "Rules, in order of importance:\n"
    "1. Only record what was actually said. If the conversation does not "
    "establish a field, return null for it. Never guess, never infer from what "
    "usually happens, never fill a field to be helpful.\n"
    "2. Record what the CALLER said, not what the agent offered. An agent "
    "asking about finance is not the caller choosing it.\n"
    "3. If the caller changed their mind, record the last thing they said.\n"
    "4. Values go in the language and form the field description asks for."
)


def transcript_text(turns: list[dict], limit: int = 120) -> str:
    """The conversation as plain text, newest kept if it is very long.

    A cap because a long call is billed by the token on every extraction, and
    the fields worth recording are almost always settled by the end.
    """
    lines = []
    for t in turns[-limit:]:
        who = "Agent" if t.get("role") == "agent" else "Caller"
        text = (t.get("text") or "").strip()
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines)


def tool_text(tool_calls: list[dict], limit: int = 6000) -> str:
    """What the tools answered, for values the conversation never spoke aloud.

    The reason this exists: a dealer lookup returns a code and a name, the agent
    reads the NAMES to the caller because nobody recites "10015" down a phone,
    and the code is therefore nowhere in the transcript. Extraction could only
    ever record the name - which is exactly what kept happening.

    Only tools whose campaign_tools row has keep_response set have anything
    stored, so this is empty unless somebody deliberately turned it on for that
    endpoint.
    """
    out, used = [], 0
    for t in tool_calls or []:
        body = (t.get("response") or "").strip()
        if not body:
            continue
        block = f"{t.get('name')}({json.dumps(t.get('arguments') or {}, ensure_ascii=False)}) returned:\n{body}"
        if used + len(block) > limit:
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out)


async def extract(*, turns: list[dict], fields: list[dict], api_key: str,
                  tool_calls: list[dict] | None = None,
                  model: str = "gpt-4.1-mini") -> dict:
    """-> {field: value}, or {} when there is nothing to extract.

    Failure is not raised. A call whose extraction fails should still deliver
    everything factual - the identifiers, the outcome, the duration - because
    that is often all the client's system actually keys on. Losing the whole
    postback because a summary could not be written would be the wrong trade.
    """
    schema = build_schema(fields)
    if not schema:
        return {}
    keys = list(schema["properties"])

    text = transcript_text(turns)
    if not text.strip():
        log.info("postback: nothing said on this call, skipping extraction")
        return {k: None for k in keys}

    # Appended rather than mixed in, and labelled, so the model can tell what a
    # person said from what a system returned. Rule 5 below leans on that.
    tools_md = tool_text(tool_calls or [])
    if tools_md:
        text = text + "\n\n=== TOOL RESULTS ===\n" + tools_md

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)
        resp = await client.chat.completions.create(
            model=model,
            # Deterministic: the same conversation must produce the same record
            # twice, or re-running an extraction becomes a coin toss.
            temperature=0,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": text}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "call_record", "strict": True,
                                "schema": schema},
            },
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        # Every configured key, every time, null where the conversation did not
        # establish one.
        #
        # These used to be dropped, on the reasoning that "absent" is the honest
        # description of a field never reached. It is - but it made the payload
        # a different shape on every call, and a client parsing it has to guard
        # each key separately and cannot tell "not discussed" from "we changed
        # the field list". A stable object with nulls in it says the same thing
        # and can be read without guarding.
        out = {k: (None if data.get(k) == "" else data.get(k)) for k in keys}
        found = sum(1 for v in out.values() if v is not None)
        log.info("postback: extracted %d of %d fields", found, len(keys))
        return out
    except Exception:
        # Still the full shape. A failed extraction must not look to the client
        # like a different message from a call where nothing was established.
        log.exception("postback extraction failed - sending the facts anyway")
        return {k: None for k in keys}


def envelope(*, call_row: dict, dialler: dict, extracted: dict,
             turns: list[dict] | None,
             tool_calls: list[dict] | None = None,
             full: bool = True) -> dict:
    """The payload.

    `full=False` sends the extracted fields alone, flat, and nothing else. That
    is what a client's existing endpoint usually expects - it asked for six
    fields and wants an object with six keys in it. Everything below exists for
    a consumer who wants to know where each value came from, which is a
    different reader with a different need.

    Split into three parts on purpose, because they have three different levels
    of trust and whoever consumes this needs to know which is which:

      call      - measured by us, always correct
      dialer    - passed through untouched from their own system
      extracted - read out of a conversation by a model, and therefore the only
                  part that can be wrong
      tools     - what the client's own API answered, verbatim

    `tools` is the answer to "are you sure the model read that code correctly".
    It is present only for tools with keep_response set, and it is the same
    bytes their API sent - so if the extraction and this disagree, this is the
    one to believe.
    """
    if not full:
        # Deliberately not merged with anything. Adding an id here would be
        # helpful right up until it collided with a field of the same name that
        # the campaign had configured.
        return dict(extracted or {})

    body: dict[str, Any] = {
        "call": {
            "id": call_row.get("id"),
            "started_at": _iso(call_row.get("started_at")),
            "ended_at": _iso(call_row.get("ended_at")),
            "duration_ms": call_row.get("duration_ms"),
            "caller": call_row.get("caller"),
            "callee": call_row.get("callee"),
            "end_reason": call_row.get("end_reason"),
            "outcome": call_row.get("outcome"),
            "transferred_to": call_row.get("transferred_to"),
            "turn_count": call_row.get("turn_count"),
            "recording_id": call_row.get("sip_call_id"),
        },
        # Keys stripped of the "dialer." prefix: it means something to us and
        # nothing to them.
        "dialer": {k.split(".", 1)[-1]: v for k, v in (dialler or {}).items()},
        "extracted": extracted or {},
    }
    kept = [t for t in (tool_calls or []) if (t.get("response") or "").strip()]
    if kept:
        body["tools"] = [
            {"name": t.get("name"), "arguments": t.get("arguments"),
             "status_code": t.get("status_code"), "response": t.get("response"),
             "at": _iso(t.get("created_at"))}
            for t in kept
        ]

    if turns is not None:
        body["transcript"] = [
            {"role": t.get("role"), "text": t.get("text"),
             "at": _iso(t.get("ts"))}
            for t in turns if (t.get("text") or "").strip()
        ]
    return body


# Every timestamp in the payload is rendered in this zone. The database stores
# timestamptz (UTC, correctly) and asyncpg hands back UTC-aware datetimes, so
# without this the client's API receives 2026-08-17T06:38:22+00:00 for a call
# that everyone involved thinks happened at 12:08 - and reconciling their
# records against ours becomes an arithmetic exercise nobody should be doing.
#
# The offset is kept in the string rather than stripped. A bare "12:08:22" is
# ambiguous the moment anything crosses a border or a DST boundary; "+05:30"
# says exactly what it means and every JSON date parser understands it.
_TZ_NAME = os.getenv("POSTBACK_TIMEZONE", "Asia/Kolkata")
try:
    POSTBACK_TZ = ZoneInfo(_TZ_NAME)
except Exception:
    # ZoneInfo needs a tz database. Rocky has one, but a container built from a
    # slim base may not, and this module failing to import would silently take
    # every postback with it. IST has never observed DST, so a fixed +05:30 is
    # not an approximation - it is the same answer.
    POSTBACK_TZ = timezone(timedelta(hours=5, minutes=30))
    log.warning("no tz database for %r - using a fixed +05:30", _TZ_NAME)


def _iso(v):
    """-> ISO 8601 in POSTBACK_TZ, or the value untouched if it is not a time."""
    if not hasattr(v, "isoformat"):
        return v
    if getattr(v, "tzinfo", None) is None:
        # A naive datetime from this database is UTC - that is what the column
        # type means. Assuming local time here would silently shift it by five
        # and a half hours.
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(POSTBACK_TZ).isoformat()
