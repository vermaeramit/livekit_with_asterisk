"""Per-campaign agent configuration.

The workers call store.load_config() inside the job entrypoint, so a save here
takes effect on the next call with no restart and no effect on calls already in
progress.
"""
from __future__ import annotations

import json

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, db
from ..deps import CurrentUser, active_user, assert_campaign_visible, require_roles
from ..schemas import (AgentConfigOut, AgentConfigUpdate, AuditEntry,
                       CampaignRoute, CampaignRouteCreate)

router = APIRouter(prefix="/campaigns/{campaign_id}", tags=["agent config"])

editor = require_roles("tenant_admin")

FIELDS = (
    "language", "greeting", "instructions",
    "stt_model", "llm_model", "llm_temperature",
    "tts_model", "tts_voice", "allow_interrupt",
    "kb_enabled", "kb_top_k", "kb_min_score", "kb_inline_max_tokens", "kb_summary",
    "max_turns", "max_duration_sec", "max_prompt_tokens", "limit_message",
    "transfer_enabled", "transfer_to", "transfer_message",
    "recording_disclosure",
)

SELECT_CONFIG = f"""
    SELECT campaign_id, name, updated_at, {', '.join(FIELDS)}
      FROM agent_config WHERE campaign_id = $1 ORDER BY id LIMIT 1
"""


async def _get(campaign_id: int) -> dict:
    row = await db.pool().fetchrow(SELECT_CONFIG, campaign_id)
    if row is None:
        # Campaigns created through the panel always get one. A campaign without
        # a config predates the panel, and cannot take a call.
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "this campaign has no agent config")
    return dict(row)


@router.get("/config", response_model=AgentConfigOut)
async def get_config(campaign_id: int, user: CurrentUser = Depends(active_user)):
    await assert_campaign_visible(user, campaign_id)
    return AgentConfigOut(**await _get(campaign_id))


@router.patch("/config", response_model=AgentConfigOut)
async def update_config(campaign_id: int, body: AgentConfigUpdate,
                        actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    before = await _get(campaign_id)

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return AgentConfigOut(**before)

    unknown = set(fields) - set(FIELDS)
    assert not unknown, f"schema and FIELDS disagree: {unknown}"

    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
    await db.pool().execute(
        f"UPDATE agent_config SET {sets}, updated_at = now() WHERE campaign_id = $1",
        campaign_id, *fields.values())

    await audit.record(actor, entity="agent_config", entity_id=before["name"],
                       action="update", tenant_id=tenant_id, campaign_id=campaign_id,
                       changes=audit.diff(before, fields))
    return AgentConfigOut(**await _get(campaign_id))


@router.get("/routes", response_model=list[CampaignRoute])
async def list_routes(campaign_id: int, user: CurrentUser = Depends(active_user)):
    await assert_campaign_visible(user, campaign_id)
    rows = await db.pool().fetch(
        """SELECT id, campaign_id, did, description, created_at
             FROM campaign_routes WHERE campaign_id = $1 ORDER BY did""",
        campaign_id)
    return [CampaignRoute(**dict(r)) for r in rows]


@router.post("/routes", response_model=CampaignRoute,
             status_code=status.HTTP_201_CREATED)
async def add_route(campaign_id: int, body: CampaignRouteCreate,
                    actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    try:
        row = await db.pool().fetchrow(
            """INSERT INTO campaign_routes (campaign_id, did, description)
               VALUES ($1, $2, $3)
               RETURNING id, campaign_id, did, description, created_at""",
            campaign_id, body.did, body.description)
    except asyncpg.UniqueViolationError:
        # DIDs are unique across every tenant. Say which campaign has it only
        # when the caller is allowed to see that campaign - otherwise the error
        # would leak another client's configuration.
        owner = await db.pool().fetchrow(
            """SELECT c.name, c.tenant_id FROM campaign_routes r
                 JOIN campaigns c ON c.id = r.campaign_id WHERE r.did = $1""",
            body.did)
        where = (f" (used by '{owner['name']}')"
                 if owner and (actor.is_superadmin
                               or owner["tenant_id"] == actor.tenant_id) else "")
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{body.did} is already routed{where}")

    await audit.record(actor, entity="campaign_route", entity_id=body.did,
                       action="create", tenant_id=tenant_id,
                       campaign_id=campaign_id,
                       changes=audit.diff(None, body.model_dump()))
    return CampaignRoute(**dict(row))


@router.delete("/routes/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_route(campaign_id: int, route_id: int,
                       actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    row = await db.pool().fetchrow(
        "SELECT did FROM campaign_routes WHERE id = $1 AND campaign_id = $2",
        route_id, campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "route not found")

    await db.pool().execute("DELETE FROM campaign_routes WHERE id = $1", route_id)
    await audit.record(actor, entity="campaign_route", entity_id=row["did"],
                       action="delete", tenant_id=tenant_id,
                       campaign_id=campaign_id,
                       changes={"did": {"from": row["did"], "to": None}})


@router.get("/audit", response_model=list[AuditEntry])
async def campaign_audit(campaign_id: int,
                         user: CurrentUser = Depends(active_user),
                         limit: int = Query(50, ge=1, le=200)):
    await assert_campaign_visible(user, campaign_id)
    rows = await db.pool().fetch(
        """SELECT a.id, a.entity, a.entity_id, a.action, a.changes, a.created_at,
                  u.email AS user_email
             FROM config_audit a LEFT JOIN users u ON u.id = a.user_id
            WHERE a.campaign_id = $1
            ORDER BY a.created_at DESC LIMIT $2""",
        campaign_id, limit)

    # changes is JSONB; asyncpg hands it back as a string unless a codec is set
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d["changes"], str):
            d["changes"] = json.loads(d["changes"])
        out.append(AuditEntry(**d))
    return out
