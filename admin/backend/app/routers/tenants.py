from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, db
from ..deps import CurrentUser, require_roles
from ..schemas import TenantCreate, TenantOut, TenantUpdate

# Tenants are the isolation boundary itself, so only we manage them - a
# tenant_admin can never see, create or rename another tenant.
router = APIRouter(prefix="/tenants", tags=["tenants"],
                   dependencies=[Depends(require_roles("superadmin"))])

LIST_SQL = """
    SELECT t.id, t.slug, t.name, t.status, t.created_at,
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
                        user: CurrentUser = Depends(require_roles("superadmin"))):
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
                        user: CurrentUser = Depends(require_roles("superadmin"))):
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
