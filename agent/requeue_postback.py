#!/usr/bin/env python3
"""Rebuild a finished call's postback and queue it for delivery.

The row is written exactly once, by the job process, in its shutdown callback.
If anything stops that process before the INSERT lands, the call's result is
lost and there has never been a way to get it back - the console's Retry button
re-SENDS an existing row and cannot create one.

Call 590 was the first one found. livekit's shutdown_process_timeout (10 s by
default) killed the process mid-extraction, so no row was written and no error
was logged - a 15-turn conversation that ended in a booked demo, which the
customer's system never heard about. The deadline itself is fixed now, in
voice_agent.py and postback.py; this is how the calls already lost come back.

It is also the thing postback.py's module docstring has always promised and
never had: "the schema can change and old calls can be re-processed". Add a
field to a campaign and the calls from before it can be rebuilt with it.

_queue_postback is IMPORTED rather than reimplemented. A second copy of the
envelope logic would drift from the live one, and the first anyone would know
of it is a client parsing two different shapes from the same endpoint.

Delivery is not done here. The row is queued and admin-api's sweeper sends it -
the same path a live call takes, and the only one that retries.

Usage, from the agent's own directory, venv and environment:

    cd /srv/aivoice/agent
    /opt/aivoice/agent/.venv/bin/python requeue_postback.py 590
    /opt/aivoice/agent/.venv/bin/python requeue_postback.py 590 591 --dry-run

Nothing here runs against a deadline, so a slow model is not a problem - but
POSTBACK_EXTRACT_TIMEOUT still applies and defaults to 20 s. Raise it for a
long call on a slow gateway:

    POSTBACK_EXTRACT_TIMEOUT=120 /opt/aivoice/agent/.venv/bin/python \
        requeue_postback.py 590
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

log = logging.getLogger("requeue")


async def _requeue(call_id: int, dry_run: bool) -> bool:
    """-> True when a row now exists that did not before."""
    import store
    import voice_agent

    pool = await store.pool()
    row = await pool.fetchrow(
        """SELECT c.id, c.config_name, c.campaign_id, c.dialer_context,
                  c.end_reason, c.turn_count, c.ended_at,
                  (SELECT 1 FROM call_postbacks p WHERE p.call_id = c.id) AS queued
             FROM calls c WHERE c.id = $1""", call_id)

    if row is None:
        log.error("call %s: no such call", call_id)
        return False
    if row["ended_at"] is None:
        log.error("call %s: still running - nothing to report yet", call_id)
        return False
    if row["queued"]:
        # Refused rather than overwritten. save_postback is ON CONFLICT DO
        # NOTHING, so this would look like it worked and change nothing; and a
        # row that already exists may have been delivered, in which case
        # rebuilding it would send the client the same call twice.
        log.error("call %s: already queued. Use Retry in the console to "
                  "re-send it, or delete the row first to rebuild it", call_id)
        return False

    cfg = await store.load_config(row["config_name"])
    if not getattr(cfg, "postback_enabled", False):
        log.error("call %s: postback is switched off on campaign '%s'",
                  call_id, row["config_name"])
        return False

    # asyncpg hands JSONB back as text - the same trap _as_config guards in
    # store.py. A string here would reach the envelope as a dialler context
    # that is one long quoted blob instead of the fields the client keys on.
    dialler = row["dialer_context"]
    if isinstance(dialler, str):
        dialler = json.loads(dialler)

    log.info("call %s: campaign=%s turns=%s end_reason=%s llm=%s/%s",
             call_id, row["config_name"], row["turn_count"], row["end_reason"],
             getattr(cfg, "llm_provider", None) or "openai", cfg.llm_model)

    if dry_run:
        log.info("call %s: dry run, nothing written", call_id)
        return False

    keys = await store.load_provider_keys(cfg.campaign_id)
    await voice_agent._queue_postback(store, cfg, call_id, keys, dialler or {})

    # Checked rather than assumed. _queue_postback never raises - that is the
    # whole point of it - so "it returned" says nothing about whether a row was
    # written. Reading it back is the only honest answer, and the absence of
    # exactly this check is what let call 590 go unnoticed.
    ok = await pool.fetchval(
        "SELECT 1 FROM call_postbacks WHERE call_id = $1", call_id)
    if ok:
        log.info("call %s: queued - admin-api will deliver it", call_id)
    else:
        log.error("call %s: NOT queued, see the error above", call_id)
    return bool(ok)


async def _run(call_ids: list[int], dry_run: bool) -> int:
    import store

    done = 0
    try:
        for call_id in call_ids:
            if await _requeue(call_id, dry_run):
                done += 1
    finally:
        await store.close()
    if dry_run:
        # Not "0 of 1 queued", which is what this used to say and reads as a
        # failure when nothing failed.
        log.info("dry run - %d call(s) checked, nothing written", len(call_ids))
        return 0
    log.info("%d of %d queued", done, len(call_ids))
    return 0 if done == len(call_ids) else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rebuild a finished call's postback and queue it.")
    ap.add_argument("call_ids", nargs="+", type=int, metavar="CALL_ID")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would be rebuilt, write nothing")
    args = ap.parse_args()

    # The agent's own logger, so the extraction's own lines - "postback:
    # extracted 5 of 10 fields" - show up here exactly as they do in the
    # journal during a live call.
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)-7s %(name)s  %(message)s")
    return asyncio.run(_run(args.call_ids, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
