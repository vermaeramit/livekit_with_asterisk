from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, db
from ..deps import (CurrentUser, active_user, assert_campaign_visible,
                    require_roles, resolve_tenant, tenant_scope)
from ..schemas import CampaignCreate, CampaignOut, CampaignUpdate

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

editor = require_roles("tenant_admin")

SELECT_CAMPAIGN = """
    SELECT c.id, c.tenant_id, c.slug, c.name, c.description, c.enabled,
           c.created_at, t.name AS tenant_name,
           (SELECT count(*) FROM calls cl WHERE cl.campaign_id = c.id) AS call_count,
           (SELECT ac.name FROM agent_config ac WHERE ac.campaign_id = c.id
             ORDER BY ac.id LIMIT 1) AS config_name
      FROM campaigns c JOIN tenants t ON t.id = c.tenant_id
"""


async def _get(campaign_id: int) -> dict:
    row = await db.pool().fetchrow(SELECT_CAMPAIGN + " WHERE c.id = $1", campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found")
    return dict(row)


@router.get("", response_model=list[CampaignOut])
async def list_campaigns(
    user: CurrentUser = Depends(active_user),
    tenant_id: int | None = Query(None, description="superadmin only"),
):
    scope = tenant_scope(user, tenant_id)
    if scope is None:
        rows = await db.pool().fetch(SELECT_CAMPAIGN + " ORDER BY t.name, c.name")
    else:
        rows = await db.pool().fetch(
            SELECT_CAMPAIGN + " WHERE c.tenant_id = $1 ORDER BY c.name", scope)
    return [CampaignOut(**dict(r)) for r in rows]


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: int, user: CurrentUser = Depends(active_user)):
    await assert_campaign_visible(user, campaign_id)
    return CampaignOut(**await _get(campaign_id))


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
async def create_campaign(body: CampaignCreate, actor: CurrentUser = Depends(editor)):
    tenant_id = resolve_tenant(actor, body.tenant_id)

    tenant = await db.pool().fetchrow(
        "SELECT slug FROM tenants WHERE id = $1", tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")

    # A campaign without an agent_config cannot take a call, so the two are
    # created together. The name is what the workers key on today
    # (agent_config.name), and stays stable when migration 003 switches them to
    # campaign_id - which is why it is derived from the slugs, not the labels.
    config_name = f"{tenant['slug']}-{body.slug}"

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            try:
                campaign_id = await conn.fetchval(
                    """INSERT INTO campaigns (tenant_id, slug, name, description)
                       VALUES ($1, $2, $3, $4) RETURNING id""",
                    tenant_id, body.slug, body.name, body.description)
            except asyncpg.UniqueViolationError:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"this tenant already has a campaign with the slug '{body.slug}'")

            try:
                # The providers and models are set EXPLICITLY, not left to the
                # column defaults. Those defaults dated from the original Google
                # plan, so a campaign created here inherited
                # llm_model='gemini-flash-latest' and died with 404
                # model_not_found on its first call - born broken, and invisible
                # until someone dialled it. Migration 006 fixed the defaults;
                # naming them here means a future default drift cannot do it
                # again.
                await conn.execute(
                    """INSERT INTO agent_config
                           (name, campaign_id, instructions, greeting,
                            stt_provider, stt_model,
                            llm_provider, llm_model,
                            tts_provider, tts_model, tts_voice)
                       VALUES ($1, $2, $3, $4,
                               'sarvam', 'saarika:v2.5',
                               'openai', 'gpt-4.1-mini',
                               'sarvam', 'bulbul:v3', 'shubh')""",
                    config_name, campaign_id,
                    "You are a helpful voice assistant. Keep answers short and "
                    "natural for a phone call.",
                    "Namaste! Main aapki kaise madad kar sakta hoon?")
            except asyncpg.UniqueViolationError:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"an agent config named '{config_name}' already exists")

    await audit.record(actor, entity="campaign", entity_id=campaign_id,
                       action="create", tenant_id=tenant_id, campaign_id=campaign_id,
                       changes=audit.diff(None, body.model_dump()))
    return CampaignOut(**await _get(campaign_id))


@router.patch("/{campaign_id}", response_model=CampaignOut)
async def update_campaign(campaign_id: int, body: CampaignUpdate,
                          actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return CampaignOut(**await _get(campaign_id))

    before = await _get(campaign_id)
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
    await db.pool().execute(
        f"UPDATE campaigns SET {sets}, updated_at = now() WHERE id = $1",
        campaign_id, *fields.values())

    action = ("disable" if fields.get("enabled") is False
              else "enable" if fields.get("enabled") is True else "update")
    await audit.record(actor, entity="campaign", entity_id=campaign_id, action=action,
                       tenant_id=tenant_id, campaign_id=campaign_id,
                       changes=audit.diff(before, fields))
    return CampaignOut(**await _get(campaign_id))


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(campaign_id: int, actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    row = await _get(campaign_id)

    # Deleting cascades into the knowledge base and the agent config, and
    # detaches historical calls. Once a campaign has real call history that is
    # destroying evidence, not tidying up - disable it instead.
    if row["call_count"] > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"this campaign has {row['call_count']} calls on record; "
            "disable it instead of deleting it")

    await db.pool().execute("DELETE FROM campaigns WHERE id = $1", campaign_id)
    await audit.record(actor, entity="campaign", entity_id=campaign_id,
                       action="delete", tenant_id=tenant_id,
                       changes={"slug": {"from": row["slug"], "to": None}})
