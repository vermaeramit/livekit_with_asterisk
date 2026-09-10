"""One feed of everything worth knowing about, newest first.

Six things already record themselves and each lives on its own page or on no
page at all: config changes, alerts, provider failures, postbacks that could not
be delivered, tool calls that errored, and logins. Answering "what happened
yesterday afternoon" meant opening five tabs and knowing which five.

CALLS ARE DELIBERATELY NOT IN HERE. There are hundreds of them and they would
bury everything else, which is the failure mode of every activity feed that
tries to show all activity. The Calls page exists and is better at it. What is
here is what asks for attention.

THE QUERY SHAPE MATTERS. Each source is limited and ordered on its own index
BEFORE the union, so Postgres never sorts six whole tables to return fifty rows.
This runs on the same database the agent writes turns into during live calls,
and a feed that made the agent wait would be a monitoring page that caused the
thing it monitors.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from .. import db
from ..deps import CurrentUser, active_user, tenant_scope
from ..schemas import ActivityEvent, ActivityFeed

log = logging.getLogger("admin-api")
router = APIRouter(tags=["activity"])

# What each source is called in the filter, and how loud it is by default.
KINDS = ("alert", "error", "config", "postback", "tool", "login")

# Per source, before the union. Fifty of any one kind is more than anybody
# reads in a sitting, and it keeps each subquery on its own index.
_PER_SOURCE = 60


@router.get("/activity", response_model=ActivityFeed)
async def activity(user: CurrentUser = Depends(active_user),
                   tenant_id: int | None = None,
                   kind: str | None = Query(None, description="one of KINDS"),
                   hours: int = Query(24, ge=1, le=720),
                   limit: int = Query(60, ge=1, le=200)):
    """Everything that asked for attention, across the whole system."""
    scope = tenant_scope(user, tenant_id)
    wanted = [k for k in KINDS if kind in (None, k)]

    parts: list[str] = []
    args: list = [hours]                      # $1 everywhere below
    tid = None
    if scope is not None:
        args.append(scope)
        tid = f"${len(args)}"

    def scoped(col: str) -> str:
        """A tenant filter, or nothing at all for a superadmin."""
        return f" AND {col} = {tid}" if tid else ""

    if "alert" in wanted:
        parts.append(f"""
            SELECT 'alert' AS kind, a.severity, a.created_at AS at,
                   a.tenant_id, a.campaign_id,
                   a.kind AS title, a.message AS detail,
                   NULL::text AS actor,
                   CASE WHEN a.delivery = 'failed'
                        THEN 'not delivered: ' || coalesce(a.delivery_error, '')
                        END AS extra
              FROM alerts a
             WHERE a.created_at > now() - ($1 || ' hours')::interval
                   {scoped('a.tenant_id')}
             ORDER BY a.created_at DESC LIMIT {_PER_SOURCE}""")

    if "error" in wanted:
        # A provider gave up mid-call. The single most useful row in here,
        # because it is the one a caller felt.
        parts.append(f"""
            SELECT 'error' AS kind, 'critical' AS severity, e.created_at AS at,
                   e.tenant_id, e.campaign_id,
                   e.source || coalesce(' · ' || e.provider, '') AS title,
                   e.message AS detail,
                   NULL::text AS actor,
                   CASE WHEN e.code IS NOT NULL
                        THEN 'HTTP ' || e.code::text END AS extra
              FROM call_errors e
             WHERE e.created_at > now() - ($1 || ' hours')::interval
                   {scoped('e.tenant_id')}
             ORDER BY e.created_at DESC LIMIT {_PER_SOURCE}""")

    if "config" in wanted:
        # Who changed what. This is the answer to "it was fine on Tuesday".
        parts.append(f"""
            SELECT 'config' AS kind, 'info' AS severity, c.created_at AS at,
                   c.tenant_id, c.campaign_id,
                   c.entity || ' ' || c.action AS title,
                   coalesce(c.entity_id, '') AS detail,
                   u.email AS actor,
                   -- The field names only. The VALUES can be a prompt, a
                   -- postback URL or the before-and-after of a secret, and a
                   -- feed is the wrong place to put any of them.
                   (SELECT string_agg(k, ', ' ORDER BY k)
                      FROM jsonb_object_keys(c.changes) AS k) AS extra
              FROM config_audit c
              LEFT JOIN users u ON u.id = c.user_id
             WHERE c.created_at > now() - ($1 || ' hours')::interval
                   {scoped('c.tenant_id')}
             ORDER BY c.created_at DESC LIMIT {_PER_SOURCE}""")

    if "postback" in wanted:
        # Only the ones that did not arrive. A delivered postback is not news.
        parts.append(f"""
            -- created_at, because this table has no updated_at: it carries
            -- created_at, sent_at and next_attempt_at, and the last of those
            -- is a time in the FUTURE. So a postback that has been failing all
            -- morning sits at the moment its call ended, which is the honest
            -- answer to "which call has not arrived" even if it is not the
            -- moment of the latest failure. The attempt count beside it says
            -- how long it has been trying.
            SELECT 'postback' AS kind, 'warning' AS severity,
                   p.created_at AS at, cam.tenant_id, p.campaign_id,
                   'postback ' || p.status AS title,
                   coalesce(p.last_error, 'no error recorded') AS detail,
                   NULL::text AS actor,
                   'call ' || p.call_id::text || ' · attempt ' || p.attempts::text AS extra
              FROM call_postbacks p
              LEFT JOIN campaigns cam ON cam.id = p.campaign_id
             WHERE p.status <> 'sent'
               AND p.created_at > now() - ($1 || ' hours')::interval
                   {scoped('cam.tenant_id')}
             ORDER BY p.created_at DESC LIMIT {_PER_SOURCE}""")

    if "tool" in wanted:
        # The customer's own API refusing, which looks to a caller exactly like
        # the agent being broken.
        parts.append(f"""
            SELECT 'tool' AS kind, 'warning' AS severity, t.created_at AS at,
                   cl.tenant_id, cl.campaign_id,
                   'tool ' || t.name AS title,
                   coalesce(t.error, 'HTTP ' || t.status_code::text) AS detail,
                   NULL::text AS actor,
                   'call ' || t.call_id::text AS extra
              FROM tool_invocations t
              LEFT JOIN calls cl ON cl.id = t.call_id
             WHERE (t.error IS NOT NULL OR t.status_code >= 400)
               AND t.created_at > now() - ($1 || ' hours')::interval
                   {scoped('cl.tenant_id')}
             ORDER BY t.created_at DESC LIMIT {_PER_SOURCE}""")

    if "login" in wanted:
        parts.append(f"""
            SELECT 'login' AS kind, 'info' AS severity, s.created_at AS at,
                   u.tenant_id, NULL::bigint AS campaign_id,
                   'signed in' AS title, u.email AS detail,
                   u.email AS actor,
                   host(s.ip) AS extra
              FROM user_sessions s
              JOIN users u ON u.id = s.user_id
             WHERE s.created_at > now() - ($1 || ' hours')::interval
                   {scoped('u.tenant_id')}
             ORDER BY s.created_at DESC LIMIT {_PER_SOURCE}""")

    if not parts:
        return ActivityFeed(events=[], hours=hours)

    args.append(limit)
    sql = (" UNION ALL ".join(parts)
           + f" ORDER BY at DESC LIMIT ${len(args)}")
    rows = await db.pool().fetch(sql, *args)

    # Campaign names, resolved once rather than joined into six subqueries.
    ids = {r["campaign_id"] for r in rows if r["campaign_id"] is not None}
    names: dict[int, str] = {}
    if ids:
        names = {r["id"]: r["name"] for r in await db.pool().fetch(
            "SELECT id, name FROM campaigns WHERE id = ANY($1::bigint[])",
            list(ids))}

    return ActivityFeed(
        hours=hours,
        events=[ActivityEvent(
            kind=r["kind"], severity=r["severity"], at=r["at"],
            title=r["title"], detail=r["detail"], actor=r["actor"],
            extra=r["extra"], campaign_id=r["campaign_id"],
            campaign=names.get(r["campaign_id"])) for r in rows])
