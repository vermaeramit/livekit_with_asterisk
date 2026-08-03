"""Read-only lookups the UI needs to build filters.

Full CRUD for campaigns and tenants lands in Phase 2; this is deliberately the
minimum a dropdown needs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import db
from ..deps import CurrentUser, current_user, require_roles, tenant_scope

router = APIRouter(tags=["meta"])


@router.get("/campaigns")
async def list_campaigns(
    user: CurrentUser = Depends(current_user),
    tenant_id: int | None = Query(None, description="superadmin only"),
):
    scope = tenant_scope(user, tenant_id)
    if scope is None:
        rows = await db.pool().fetch(
            """SELECT c.id, c.tenant_id, c.slug, c.name, c.enabled,
                      t.name AS tenant_name
                 FROM campaigns c JOIN tenants t ON t.id = c.tenant_id
                ORDER BY t.name, c.name""")
    else:
        rows = await db.pool().fetch(
            """SELECT c.id, c.tenant_id, c.slug, c.name, c.enabled,
                      t.name AS tenant_name
                 FROM campaigns c JOIN tenants t ON t.id = c.tenant_id
                WHERE c.tenant_id = $1 ORDER BY c.name""", scope)
    return [dict(r) for r in rows]


@router.get("/tenants", dependencies=[Depends(require_roles("superadmin"))])
async def list_tenants():
    rows = await db.pool().fetch(
        """SELECT t.id, t.slug, t.name, t.status, t.created_at,
                  count(c.id) AS campaign_count
             FROM tenants t LEFT JOIN campaigns c ON c.tenant_id = t.id
            GROUP BY t.id ORDER BY t.name""")
    return [dict(r) for r in rows]
