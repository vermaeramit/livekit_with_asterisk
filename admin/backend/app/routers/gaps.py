"""What the bot could not answer, grouped by the question.

Rows are written one per occurrence, because each one belongs to a call and
that link is what lets somebody go and listen. But nobody works from a list of
occurrences - they work from "this was asked fourteen times", which is why
everything here groups by the question before it reaches the console.

The order is deliberate: most unanswered occurrences first. That is the order
you would fill the gaps in, so it is the order the page arrives in.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, db
from ..deps import CurrentUser, active_user, tenant_scope
from ..schemas import GapAcknowledge, KnowledgeGapOut

router = APIRouter(tags=["gaps"])

_GROUPED = """
    SELECT g.tenant_id, t.name AS tenant_name,
           g.campaign_id, c.name AS campaign_name,
           g.kind, g.query_key,
           (array_agg(g.query  ORDER BY g.created_at DESC))[1] AS query,
           (array_agg(g.detail ORDER BY g.created_at DESC))[1] AS detail,
           count(*)                                            AS occurrences,
           count(*) FILTER (WHERE g.acknowledged_at IS NULL)   AS open_occurrences,
           min(g.created_at)                                   AS first_seen,
           max(g.created_at)                                   AS last_seen,
           min(g.best_score)                                   AS worst_score,
           -- A handful is enough to go and listen to; the rest are the same
           -- question again.
           (array_agg(g.call_id ORDER BY g.created_at DESC)
                FILTER (WHERE g.call_id IS NOT NULL))[1:5]     AS call_ids,
           max(g.acknowledged_at)                              AS acknowledged_at,
           (array_agg(u.email ORDER BY g.acknowledged_at DESC NULLS LAST)
                FILTER (WHERE u.email IS NOT NULL))[1]         AS acknowledged_by_email,
           (array_agg(g.note ORDER BY g.acknowledged_at DESC NULLS LAST)
                FILTER (WHERE g.note IS NOT NULL))[1]          AS note
      FROM knowledge_gaps g
      JOIN tenants t ON t.id = g.tenant_id
      LEFT JOIN campaigns c ON c.id = g.campaign_id
      LEFT JOIN users u ON u.id = g.acknowledged_by
     {where}
     GROUP BY g.tenant_id, t.name, g.campaign_id, c.name, g.kind, g.query_key
     {having}
     ORDER BY count(*) FILTER (WHERE g.acknowledged_at IS NULL) DESC,
              max(g.created_at) DESC
     LIMIT {limit}
"""


@router.get("/gaps", response_model=list[KnowledgeGapOut])
async def list_gaps(user: CurrentUser = Depends(active_user),
                    tenant_id: int | None = None,
                    campaign_id: int | None = None,
                    kind: str | None = Query(None, pattern="^[a-z_]{3,20}$"),
                    open_only: bool = True,
                    limit: int = Query(200, ge=1, le=1000)):
    scope = tenant_scope(user, tenant_id)
    where, args = [], []
    if scope is not None:
        args.append(scope)
        where.append(f"g.tenant_id = ${len(args)}")
    if campaign_id is not None:
        args.append(campaign_id)
        where.append(f"g.campaign_id = ${len(args)}")
    if kind:
        args.append(kind)
        where.append(f"g.kind = ${len(args)}")

    args.append(limit)
    sql = _GROUPED.format(
        where=("WHERE " + " AND ".join(where)) if where else "",
        # Filtered after grouping, not before: a group that is half handled
        # should still show, with the count of what is still open.
        having="HAVING count(*) FILTER (WHERE g.acknowledged_at IS NULL) > 0"
               if open_only else "",
        limit=f"${len(args)}")
    rows = await db.pool().fetch(sql, *args)
    return [KnowledgeGapOut(**dict(r)) for r in rows]


@router.get("/gaps/unread-count")
async def unread_count(user: CurrentUser = Depends(active_user)):
    """The sidebar badge. Counts QUESTIONS, not occurrences.

    Twenty people asking the same thing is one gap to fill, and a badge reading
    20 would send someone looking for twenty pieces of work that do not exist.
    """
    scope = tenant_scope(user)
    sql = ("SELECT count(*) FROM (SELECT 1 FROM knowledge_gaps "
           "WHERE acknowledged_at IS NULL {extra} "
           "GROUP BY campaign_id, kind, query_key) q")
    if scope is None:
        n = await db.pool().fetchval(sql.format(extra=""))
    else:
        n = await db.pool().fetchval(sql.format(extra="AND tenant_id = $1"), scope)
    return {"count": n}


@router.post("/gaps/acknowledge", response_model=list[KnowledgeGapOut])
async def acknowledge(body: GapAcknowledge,
                      actor: CurrentUser = Depends(active_user)):
    """Mark every open occurrence of one question as handled.

    Only what is open right now. An occurrence recorded tomorrow is a new row
    and the question comes back - which is the point: it is fresh evidence that
    whatever was done did not fill the gap.
    """
    where = ["acknowledged_at IS NULL", "kind = $1", "query_key = $2"]
    args: list = [body.kind, body.query_key.strip().lower()]
    if body.campaign_id is None:
        where.append("campaign_id IS NULL")
    else:
        args.append(body.campaign_id)
        where.append(f"campaign_id = ${len(args)}")
    if not actor.is_superadmin:
        args.append(actor.tenant_id)
        where.append(f"tenant_id = ${len(args)}")

    args.extend([actor.id, (body.note or "").strip() or None])
    n = await db.pool().fetchval(
        f"""WITH done AS (
                UPDATE knowledge_gaps
                   SET acknowledged_at = now(),
                       acknowledged_by = ${len(args) - 1},
                       note = ${len(args)}
                 WHERE {' AND '.join(where)}
             RETURNING 1)
            SELECT count(*) FROM done""", *args)
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "nothing open matches that question")

    await audit.record(actor, entity="knowledge_gap", entity_id=body.query_key,
                       action="acknowledge",
                       changes={"occurrences": {"from": n, "to": 0}})
    return await list_gaps(user=actor, campaign_id=body.campaign_id,
                           kind=body.kind, open_only=False, limit=50)
