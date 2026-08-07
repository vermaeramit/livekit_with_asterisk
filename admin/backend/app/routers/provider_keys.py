"""Provider key endpoints.

Write-only by design. A key goes in through PUT and never comes back out: no
endpoint here returns one, and the audit trail records that a key changed, not
what it changed to. The console works from `hint` (last four characters) alone,
which is enough to answer "is this the key I just pasted?" and useless to anyone
who gets hold of the response.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, db, provider_keys as pk, secretlib
from ..deps import (CurrentUser, active_user, assert_campaign_visible,
                    require_roles, tenant_scope)
from ..schemas import ProviderKeyOut, ProviderKeySet, ProviderKeyWritten

router = APIRouter(tags=["provider keys"])

editor = require_roles("tenant_admin")


def _check_provider(provider: str) -> str:
    if provider not in pk.PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"unknown provider '{provider}'")
    return provider


def _require_crypto() -> None:
    if not secretlib.available():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"provider key encryption unavailable: {secretlib.why_unavailable()}")


async def _assert_tenant_visible(user: CurrentUser, tenant_id: int) -> int:
    # tenant_scope refuses a mismatch; the existence check keeps a superadmin
    # from writing keys for a tenant that was deleted mid-session.
    tenant_scope(user, tenant_id)
    exists = await db.pool().fetchval(
        "SELECT 1 FROM tenants WHERE id = $1", tenant_id)
    if not exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    return tenant_id


# --------------------------------------------------------------------------
# client-level: the default for every campaign the client owns
# --------------------------------------------------------------------------

@router.get("/clients/{tenant_id}/keys", response_model=list[ProviderKeyOut])
async def list_client_keys(tenant_id: int,
                           user: CurrentUser = Depends(active_user)):
    await _assert_tenant_visible(user, tenant_id)
    return [ProviderKeyOut(**r)
            for r in await pk.status_for(tenant_id=tenant_id, campaign_id=None)]


@router.put("/clients/{tenant_id}/keys/{provider}",
            response_model=ProviderKeyWritten)
async def set_client_key(tenant_id: int, provider: str, body: ProviderKeySet,
                         actor: CurrentUser = Depends(editor)):
    _require_crypto()
    _check_provider(provider)
    await _assert_tenant_visible(actor, tenant_id)

    result = await pk.validate(provider, body.key)
    if not result.ok:
        # 422, not 400: the value is well-formed, the provider disagrees with it.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.message)

    hint = await pk.store(tenant_id=tenant_id, campaign_id=None,
                          provider=provider, key=body.key, actor_id=actor.id)
    # The hint, never the key. This table is readable by every tenant admin in
    # the tenant.
    await audit.record(actor, entity="provider_key", entity_id=provider,
                       action="set", tenant_id=tenant_id,
                       changes={"scope": "client", "hint": hint})
    return ProviderKeyWritten(provider=provider, hint=hint,
                              message=result.message,
                              no_credits=result.no_credits)


@router.delete("/clients/{tenant_id}/keys/{provider}",
               status_code=status.HTTP_204_NO_CONTENT)
async def delete_client_key(tenant_id: int, provider: str,
                            actor: CurrentUser = Depends(editor)):
    _check_provider(provider)
    await _assert_tenant_visible(actor, tenant_id)
    if not await pk.remove(tenant_id=tenant_id, campaign_id=None,
                           provider=provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no key set")
    await audit.record(actor, entity="provider_key", entity_id=provider,
                       action="delete", tenant_id=tenant_id,
                       changes={"scope": "client"})


# --------------------------------------------------------------------------
# campaign-level: an override for one campaign
# --------------------------------------------------------------------------

@router.get("/campaigns/{campaign_id}/keys", response_model=list[ProviderKeyOut])
async def list_campaign_keys(campaign_id: int,
                             user: CurrentUser = Depends(active_user)):
    tenant_id = await assert_campaign_visible(user, campaign_id)
    return [ProviderKeyOut(**r)
            for r in await pk.status_for(tenant_id=tenant_id,
                                         campaign_id=campaign_id)]


@router.put("/campaigns/{campaign_id}/keys/{provider}",
            response_model=ProviderKeyWritten)
async def set_campaign_key(campaign_id: int, provider: str, body: ProviderKeySet,
                           actor: CurrentUser = Depends(editor)):
    _require_crypto()
    _check_provider(provider)
    tenant_id = await assert_campaign_visible(actor, campaign_id)

    result = await pk.validate(provider, body.key)
    if not result.ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.message)

    hint = await pk.store(tenant_id=tenant_id, campaign_id=campaign_id,
                          provider=provider, key=body.key, actor_id=actor.id)
    await audit.record(actor, entity="provider_key", entity_id=provider,
                       action="set", tenant_id=tenant_id, campaign_id=campaign_id,
                       changes={"scope": "campaign", "hint": hint})
    return ProviderKeyWritten(provider=provider, hint=hint,
                              message=result.message,
                              no_credits=result.no_credits)


@router.delete("/campaigns/{campaign_id}/keys/{provider}",
               status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign_key(campaign_id: int, provider: str,
                              actor: CurrentUser = Depends(editor)):
    """Remove the override. The campaign falls back to the client's key."""
    _check_provider(provider)
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    if not await pk.remove(tenant_id=tenant_id, campaign_id=campaign_id,
                           provider=provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "this campaign has no override for that provider")
    await audit.record(actor, entity="provider_key", entity_id=provider,
                       action="delete", tenant_id=tenant_id,
                       campaign_id=campaign_id, changes={"scope": "campaign"})
