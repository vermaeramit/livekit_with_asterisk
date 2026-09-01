from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import alerting, audit, db
from ..deps import (CurrentUser, active_user, require_perm, resolve_tenant,
                    tenant_scope)
from ..schemas import AlertOut, AlertRuleOut, AlertRuleUpdate, WebhookUpdate

router = APIRouter(tags=["alerts"])

editor = require_perm("campaign.write")


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(user: CurrentUser = Depends(active_user),
                      tenant_id: int | None = None,
                      unacknowledged: bool = False,
                      limit: int = Query(100, ge=1, le=500)):
    scope = tenant_scope(user, tenant_id)
    where, args = [], []
    if scope is not None:
        args.append(scope)
        where.append(f"a.tenant_id = ${len(args)}")
    if unacknowledged:
        where.append("a.acknowledged_at IS NULL")
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    args.append(limit)
    rows = await db.pool().fetch(f"""
        SELECT a.id, a.tenant_id, a.campaign_id, a.kind, a.severity, a.message,
               a.value, a.threshold, a.delivery, a.delivery_error, a.created_at,
               a.acknowledged_at, t.name AS tenant_name, c.name AS campaign_name,
               u.email AS acknowledged_by_email
          FROM alerts a
          JOIN tenants t ON t.id = a.tenant_id
          LEFT JOIN campaigns c ON c.id = a.campaign_id
          LEFT JOIN users u ON u.id = a.acknowledged_by
          {clause}
         ORDER BY a.created_at DESC LIMIT ${len(args)}""", *args)
    return [AlertOut(**dict(r)) for r in rows]


@router.get("/alerts/unread-count")
async def unread_count(user: CurrentUser = Depends(active_user)):
    """Drives the sidebar badge, so it is deliberately one cheap query."""
    scope = tenant_scope(user)
    if scope is None:
        n = await db.pool().fetchval(
            "SELECT count(*) FROM alerts WHERE acknowledged_at IS NULL")
    else:
        n = await db.pool().fetchval(
            "SELECT count(*) FROM alerts WHERE acknowledged_at IS NULL "
            "AND tenant_id = $1", scope)
    return {"count": n}


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
async def acknowledge(alert_id: int, actor: CurrentUser = Depends(active_user)):
    row = await db.pool().fetchrow(
        "SELECT tenant_id FROM alerts WHERE id = $1", alert_id)
    if row is None or (not actor.is_superadmin
                       and row["tenant_id"] != actor.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")

    # Only the first acknowledgement counts, so a second click does not rewrite
    # who dealt with it.
    await db.pool().execute(
        "UPDATE alerts SET acknowledged_at = now(), acknowledged_by = $2 "
        "WHERE id = $1 AND acknowledged_at IS NULL", alert_id, actor.id)
    return await _one(alert_id)


async def _one(alert_id: int) -> AlertOut:
    row = await db.pool().fetchrow("""
        SELECT a.id, a.tenant_id, a.campaign_id, a.kind, a.severity, a.message,
               a.value, a.threshold, a.delivery, a.delivery_error, a.created_at,
               a.acknowledged_at, t.name AS tenant_name, c.name AS campaign_name,
               u.email AS acknowledged_by_email
          FROM alerts a
          JOIN tenants t ON t.id = a.tenant_id
          LEFT JOIN campaigns c ON c.id = a.campaign_id
          LEFT JOIN users u ON u.id = a.acknowledged_by
         WHERE a.id = $1""", alert_id)
    return AlertOut(**dict(row))


@router.get("/alert-rules", response_model=list[AlertRuleOut])
async def list_rules(user: CurrentUser = Depends(active_user),
                     tenant_id: int | None = None):
    scope = tenant_scope(user, tenant_id)
    if scope is None:
        rows = await db.pool().fetch("""
            SELECT r.*, t.name AS tenant_name, c.name AS campaign_name
              FROM alert_rules r JOIN tenants t ON t.id = r.tenant_id
              LEFT JOIN campaigns c ON c.id = r.campaign_id
             ORDER BY t.name, r.kind""")
    else:
        rows = await db.pool().fetch("""
            SELECT r.*, t.name AS tenant_name, c.name AS campaign_name
              FROM alert_rules r JOIN tenants t ON t.id = r.tenant_id
              LEFT JOIN campaigns c ON c.id = r.campaign_id
             WHERE r.tenant_id = $1 ORDER BY r.kind""", scope)
    return [AlertRuleOut(**dict(r)) for r in rows]


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleOut)
async def update_rule(rule_id: int, body: AlertRuleUpdate,
                      actor: CurrentUser = Depends(editor)):
    row = await db.pool().fetchrow(
        "SELECT tenant_id, kind, threshold, enabled FROM alert_rules WHERE id = $1",
        rule_id)
    if row is None or (not actor.is_superadmin
                       and row["tenant_id"] != actor.tenant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return await _rule(rule_id)

    # Changing a threshold should re-arm the rule. Otherwise raising it to stop
    # the noise leaves `firing` set and the next real breach stays silent.
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
    await db.pool().execute(
        f"UPDATE alert_rules SET {sets}, firing = false, updated_at = now() "
        "WHERE id = $1", rule_id, *fields.values())

    await audit.record(actor, entity="alert_rule", entity_id=row["kind"],
                       action="update", tenant_id=row["tenant_id"],
                       changes=audit.diff(dict(row), fields))
    return await _rule(rule_id)


async def _rule(rule_id: int) -> AlertRuleOut:
    row = await db.pool().fetchrow("""
        SELECT r.*, t.name AS tenant_name, c.name AS campaign_name
          FROM alert_rules r JOIN tenants t ON t.id = r.tenant_id
          LEFT JOIN campaigns c ON c.id = r.campaign_id
         WHERE r.id = $1""", rule_id)
    return AlertRuleOut(**dict(row))


@router.put("/alert-webhook")
async def set_webhook(body: WebhookUpdate, actor: CurrentUser = Depends(editor),
                      tenant_id: int | None = None):
    """Where this client's alerts are posted. Slack, Teams or any JSON endpoint."""
    target = resolve_tenant(actor, tenant_id)
    await db.pool().execute(
        "UPDATE tenants SET webhook_url = $2, updated_at = now() WHERE id = $1",
        target, body.webhook_url)
    # The URL itself is a credential - anyone holding it can post into the
    # channel - so it is never echoed back or written to the audit trail.
    await audit.record(actor, entity="tenant", entity_id=target,
                       action="set_webhook", tenant_id=target)
    return {"configured": bool(body.webhook_url)}


@router.get("/alert-webhook")
async def get_webhook(actor: CurrentUser = Depends(editor),
                      tenant_id: int | None = None):
    target = resolve_tenant(actor, tenant_id)
    url = await db.pool().fetchval(
        "SELECT webhook_url FROM tenants WHERE id = $1", target)
    # Enough to recognise which endpoint is set, not enough to reuse it.
    return {"configured": bool(url),
            "hint": (url[:30] + "…") if url else None}


@router.post("/alert-rules/evaluate")
async def evaluate_now(_: CurrentUser = Depends(require_perm("system.manage"))):
    """Run the evaluator immediately instead of waiting for the next cycle."""
    return {"raised": await alerting.evaluate_once()}
