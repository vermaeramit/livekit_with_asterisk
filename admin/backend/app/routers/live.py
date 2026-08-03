"""Calls in progress.

Read from `calls` rather than the LiveKit API: the rows are already tenant-scoped
and already carry the campaign, caller and per-turn latency. Asking LiveKit would
mean reconciling two sources of truth for no extra information.

A call is "in progress" while ended_at is NULL. That is also how a crashed worker
looks, so staleness is computed rather than trusted - see STALE_FACTOR.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import db
from ..deps import CurrentUser, active_user, tenant_scope
from ..schemas import LiveCall, LiveSummary

router = APIRouter(prefix="/live", tags=["live"])

# A worker that dies mid-call never writes ended_at, so the row sits open
# forever. Past its own duration guardrail plus half again, it is almost
# certainly abandoned - flagged rather than hidden, because a stuck call that
# silently disappears from the list is worse than one that looks wrong.
STALE_FACTOR = 1.5


@router.get("/calls", response_model=LiveSummary)
async def live_calls(user: CurrentUser = Depends(active_user),
                     tenant_id: int | None = None):
    scope = tenant_scope(user, tenant_id)

    where = ["c.ended_at IS NULL"]
    args: list = []
    if scope is not None:
        args.append(scope)
        where.append(f"c.tenant_id = ${len(args)}")
    clause = " AND ".join(where)

    rows = await db.pool().fetch(f"""
        SELECT c.id, c.started_at, c.caller, c.callee, c.language,
               c.campaign_id, cam.name AS campaign_name, c.tenant_id,
               EXTRACT(EPOCH FROM (now() - c.started_at))::int AS elapsed_sec,
               COALESCE(ac.max_duration_sec, 600)              AS max_duration_sec,
               (SELECT count(*) FROM turns t WHERE t.call_id = c.id) AS turn_count,
               (SELECT t.total_ms FROM turns t
                 WHERE t.call_id = c.id AND t.total_ms IS NOT NULL
                 ORDER BY t.seq DESC LIMIT 1)                  AS last_latency_ms,
               (SELECT t.text FROM turns t
                 WHERE t.call_id = c.id AND t.text IS NOT NULL
                 ORDER BY t.seq DESC LIMIT 1)                  AS last_text
          FROM calls c
          LEFT JOIN campaigns cam   ON cam.id = c.campaign_id
          LEFT JOIN agent_config ac ON ac.campaign_id = c.campaign_id
         WHERE {clause}
         ORDER BY c.started_at""", *args)

    calls = []
    for r in rows:
        d = dict(r)
        d["stale"] = d["elapsed_sec"] > d["max_duration_sec"] * STALE_FACTOR
        calls.append(LiveCall(**d))

    active = [c for c in calls if not c.stale]
    return LiveSummary(
        calls=calls,
        active=len(active),
        stale=len(calls) - len(active),
        # Capacity is measured, not assumed: 10 concurrent held p50 2001 ms /
        # p95 2776 ms on 8 cores at ~27% load. Above that is untested, so the
        # console warns rather than pretends to know.
        verified_capacity=10,
    )
