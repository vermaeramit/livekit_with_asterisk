"""Deliver finished calls to the client's API, and keep trying.

Lives here rather than in the agent for one reason: the agent's job process
exits when the call ends. It cannot retry anything, so it writes the row and
stops. This service is still running a minute later, and an hour later, which is
what a retry needs.

The rule is the same as `alerts` in migration 005 - the database row is the
source of truth and delivery is best effort. A client API that is down costs a
retry, never the data. Every attempt updates one row; a flapping endpoint must
not turn one call into five deliveries.

Configuration is read at SEND time, not at queue time. Fixing a wrong URL
therefore fixes the calls already waiting, which is the whole point of a queue.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request

from . import db, secretlib

log = logging.getLogger("admin-api")

SWEEP_EVERY = float(os.getenv("POSTBACK_SWEEP_SEC", "10"))
TIMEOUT = float(os.getenv("POSTBACK_TIMEOUT_SEC", "15"))
# Per sweep. A backlog drains over several passes rather than opening two
# hundred connections to an API that has just come back up.
BATCH = int(os.getenv("POSTBACK_BATCH", "20"))

# Stored tool responses age out. Not deleted - the invocation row IS the audit
# trail and must survive - only the body is nulled. A dealer list does not
# matter; the next tool somebody enables might answer with a phone number and an
# address, and this is what stops that sitting in the database forever.
RESPONSE_RETENTION_DAYS = int(os.getenv("TOOL_RESPONSE_RETENTION_DAYS", "30"))
PURGE_EVERY = 3600.0

_task: asyncio.Task | None = None


def _post(url: str, body: bytes, headers: dict) -> tuple[int | None, str]:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # The body, not just the code. A 422 naming the field it disliked is the
        # difference between a fix and a guess.
        return e.code, e.read(2000).decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


async def deliver_one(row) -> None:
    """One attempt at one row. Never raises."""
    cfg = await db.pool().fetchrow(
        """SELECT postback_enabled, postback_url, postback_auth_header,
                  postback_auth_value_enc, postback_max_attempts,
                  postback_retry_after_sec
             FROM agent_config WHERE campaign_id = $1""", row["campaign_id"])

    if cfg is None or not cfg["postback_enabled"] or not cfg["postback_url"]:
        # Turned off after the call was queued. Not a failure - there is simply
        # nowhere to send it, and marking it failed would make a deliberate
        # change look like an outage.
        await db.pool().execute(
            "UPDATE call_postbacks SET status='skipped', next_attempt_at=NULL, "
            "last_error='postback is not configured on this campaign' "
            "WHERE id=$1", row["id"])
        return

    headers = {"Content-Type": "application/json",
               "User-Agent": os.getenv("TOOL_USER_AGENT", "AIVoice-Agent/1.0")}
    if cfg["postback_auth_header"] and cfg["postback_auth_value_enc"]:
        try:
            headers[cfg["postback_auth_header"]] = secretlib.crypto().decrypt(
                cfg["postback_auth_value_enc"])
        except Exception:
            log.exception("postback auth value could not be decrypted")

    payload = row["payload"]
    body = (payload if isinstance(payload, str)
            else json.dumps(payload, default=str)).encode()

    code, text = await asyncio.to_thread(_post, cfg["postback_url"], body, headers)
    attempts = row["attempts"] + 1
    ok = code is not None and 200 <= code < 300

    if ok:
        await db.pool().execute(
            "UPDATE call_postbacks SET status='sent', attempts=$2, "
            "last_status_code=$3, last_error=NULL, sent_at=now(), "
            "next_attempt_at=NULL WHERE id=$1", row["id"], attempts, code)
        log.info("postback call=%s delivered (HTTP %s, attempt %d)",
                 row["call_id"], code, attempts)
        return

    exhausted = attempts >= (cfg["postback_max_attempts"] or 5)
    await db.pool().execute(
        """UPDATE call_postbacks
              SET status = $4, attempts = $2, last_status_code = $3,
                  last_error = $5,
                  next_attempt_at = CASE WHEN $4 = 'failed' THEN NULL
                                    ELSE now() + ($6 || ' seconds')::interval END
            WHERE id = $1""",
        row["id"], attempts, code, "failed" if exhausted else "pending",
        text[:1000] or "no response", str(cfg["postback_retry_after_sec"] or 60))

    log.warning("postback call=%s attempt %d failed (%s) - %s",
                row["call_id"], attempts, code,
                "giving up" if exhausted else "will retry")


async def sweep_once() -> int:
    rows = await db.pool().fetch(
        """SELECT id, call_id, campaign_id, payload, attempts
             FROM call_postbacks
            WHERE next_attempt_at IS NOT NULL AND next_attempt_at <= now()
            ORDER BY next_attempt_at
            LIMIT $1""", BATCH)
    for r in rows:
        try:
            await deliver_one(r)
        except Exception:
            log.exception("postback delivery raised for call %s", r["call_id"])
    return len(rows)


async def purge_old_responses() -> int:
    """Null the bodies of tool responses past their retention. Never raises."""
    if RESPONSE_RETENTION_DAYS <= 0:
        return 0
    try:
        result = await db.pool().execute(
            f"""UPDATE tool_invocations SET response = NULL
                 WHERE response IS NOT NULL
                   AND created_at < now() - interval '{RESPONSE_RETENTION_DAYS} days'""")
        n = int(result.split()[-1]) if result else 0
        if n:
            log.info("purged %d stored tool responses older than %d days",
                     n, RESPONSE_RETENTION_DAYS)
        return n
    except Exception:
        log.exception("tool response purge failed")
        return 0


async def _loop() -> None:
    log.info("postback sweeper started (every %.0fs), tool responses kept %d days",
             SWEEP_EVERY, RESPONSE_RETENTION_DAYS)
    last_purge = 0.0
    while True:
        try:
            await sweep_once()
            # Hourly, on the sweeper's own clock rather than a second task. One
            # loop that can be seen to be alive beats two that cannot.
            now = asyncio.get_running_loop().time()
            if now - last_purge > PURGE_EVERY:
                last_purge = now
                await purge_old_responses()
        except Exception:
            # Never let one bad pass stop the loop. A sweeper that dies quietly
            # looks exactly like a client API that never receives anything.
            log.exception("postback sweep failed")
        await asyncio.sleep(SWEEP_EVERY)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
