from __future__ import annotations

import hashlib
import secrets

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, db
from ..deps import CurrentUser, require_perm
from ..schemas import (TenantApiKeyCreated, TenantCreate, TenantOut,
                       TenantUpdate)

# Tenants are the isolation boundary itself, so only we manage them - a
# tenant_admin can never see, create or rename another tenant.
router = APIRouter(prefix="/tenants", tags=["tenants"],
                   dependencies=[Depends(require_perm("tenants.manage"))])

LIST_SQL = """
    SELECT t.id, t.slug, t.name, t.status, t.created_at,
           t.api_key_hint, t.api_key_set_at,
           (SELECT count(*) FROM campaigns c WHERE c.tenant_id = t.id) AS campaign_count,
           (SELECT count(*) FROM users u     WHERE u.tenant_id = t.id) AS user_count,
           (SELECT count(*) FROM calls  cl   WHERE cl.tenant_id = t.id) AS call_count
      FROM tenants t
"""


@router.get("", response_model=list[TenantOut])
async def list_tenants():
    rows = await db.pool().fetch(LIST_SQL + " ORDER BY t.name")
    return [TenantOut(**dict(r)) for r in rows]


@router.get("/{tenant_id}", response_model=TenantOut)
async def get_tenant(tenant_id: int):
    row = await db.pool().fetchrow(LIST_SQL + " WHERE t.id = $1", tenant_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
    return TenantOut(**dict(row))


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(body: TenantCreate,
                        user: CurrentUser = Depends(require_perm("tenants.manage"))):
    try:
        row = await db.pool().fetchrow(
            "INSERT INTO tenants (slug, name) VALUES ($1, $2) RETURNING id",
            body.slug, body.name)
    except asyncpg.UniqueViolationError:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"a tenant with the slug '{body.slug}' already exists")

    await audit.record(user, entity="tenant", entity_id=row["id"], action="create",
                       tenant_id=row["id"],
                       changes=audit.diff(None, body.model_dump()))
    return await get_tenant(row["id"])


@router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(tenant_id: int, body: TenantUpdate,
                        user: CurrentUser = Depends(require_perm("tenants.manage"))):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return await get_tenant(tenant_id)

    before = await db.pool().fetchrow(
        "SELECT name, status FROM tenants WHERE id = $1", tenant_id)
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")

    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
    await db.pool().execute(
        f"UPDATE tenants SET {sets}, updated_at = now() WHERE id = $1",
        tenant_id, *fields.values())

    await audit.record(user, entity="tenant", entity_id=tenant_id,
                       action="suspend" if fields.get("status") == "suspended" else "update",
                       tenant_id=tenant_id,
                       changes=audit.diff(dict(before), fields))
    return await get_tenant(tenant_id)


# There is deliberately no DELETE. Removing a tenant would cascade through its
# campaigns, users and knowledge base, and set 60+ calls' tenant_id to NULL -
# an irreversible action behind a single click. Suspend instead; suspended
# tenants cannot sign in and can be cleaned up by hand once that is really meant.


# ──────────────────── the dialler's API key, per client ────────────────────
# One key per client, not per campaign: a campaign here can carry several of the
# dialler's own ids, so a per-campaign key would leave the dialler holding a
# mapping of keys to ids that is theirs to get wrong. And not one key for
# everything, because then a leak exposes every client and a rotation stops all
# of them at once.

@router.post("/{tenant_id}/api-key", response_model=TenantApiKeyCreated)
async def create_api_key(tenant_id: int,
                         user: CurrentUser = Depends(require_perm("tenants.manage"))):
    """Generate the key, and return it exactly once.

    Stored as sha256 - the opposite of provider_keys, which are encrypted
    because they have to be handed to OpenAI. This one is only ever compared, so
    nothing needs to be able to read it back and a copy of the database yields
    no usable key.

    Calling this again REPLACES the key, which stops the dialler the moment it
    runs. That is what rotation means; the console says so before the button.
    """
    # The dlr_ prefix earns its four characters: if this ever turns up in a log,
    # a ticket or a screenshot, it is instantly identifiable and greppable as
    # this system's dialler key rather than an opaque blob nobody can place.
    key = "dlr_" + secrets.token_urlsafe(32)
    row = await db.pool().fetchrow(
        """UPDATE tenants
              SET api_key_hash = $2, api_key_hint = $3, api_key_set_at = now()
            WHERE id = $1
        RETURNING api_key_set_at""",
        tenant_id, hashlib.sha256(key.encode()).hexdigest(), key[-4:])
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")

    # The hint, never the key. This row is read by anyone who can see the audit
    # log, which is a wider audience than the one person who pressed the button.
    await audit.record(user, entity="tenant", entity_id=tenant_id,
                       action="api_key_generate", tenant_id=tenant_id,
                       changes={"hint": key[-4:]})
    return TenantApiKeyCreated(api_key=key, api_key_hint=key[-4:],
                               api_key_set_at=row["api_key_set_at"])


@router.delete("/{tenant_id}/api-key", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(tenant_id: int,
                         user: CurrentUser = Depends(require_perm("tenants.manage"))):
    """Revoke it. The capacity endpoint then refuses everything for this client.

    Worth knowing before pressing it: the dialler does not degrade, it stops
    getting an answer. Whether it then holds off dialling or dials blind is
    their code's decision, not ours.
    """
    row = await db.pool().fetchrow(
        """UPDATE tenants
              SET api_key_hash = NULL, api_key_hint = NULL, api_key_set_at = NULL
            WHERE id = $1
        RETURNING api_key_hint""", tenant_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")

    await audit.record(user, entity="tenant", entity_id=tenant_id,
                       action="api_key_revoke", tenant_id=tenant_id, changes={})
