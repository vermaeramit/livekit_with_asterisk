from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, db, security
from ..deps import (CurrentUser, require_perm,
                    resolve_tenant, tenant_scope)
from ..schemas import PasswordReset, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

# Only these two roles manage accounts at all.
manager = require_perm("users.manage")

SELECT_USER = """
    SELECT u.id, u.email, u.name, u.role, u.tenant_id, u.active,
           u.must_change_password, u.last_login_at, u.created_at,
           t.name AS tenant_name
      FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id
"""


async def _get(user_id: int) -> dict:
    row = await db.pool().fetchrow(SELECT_USER + " WHERE u.id = $1", user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return dict(row)


async def _target_or_404(actor: CurrentUser, user_id: int) -> dict:
    """Fetch a user the actor is allowed to administer.

    Everything outside the actor's reach is a 404 rather than a 403 - a distinct
    403 would confirm that a given user id exists in another tenant.
    """
    target = await _get(user_id)
    if actor.is_superadmin:
        return target
    if target["tenant_id"] != actor.tenant_id or target["role"] == "superadmin":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return target


async def _check_assignable(actor: CurrentUser, role_key: str) -> None:
    """You cannot hand out access you do not hold yourself.

    The old rule was a fixed list of three role names. That worked while roles
    were a constant in the source; now that somebody can create one, a list of
    names says nothing about what the role actually carries - a tenant admin
    could be handed a role called "desk" holding tenants.manage.

    So the test is the permissions themselves. A role is assignable if it sees
    only the actor's own client and asks for nothing the actor does not already
    have.
    """
    row = await db.pool().fetchrow(
        """SELECT r.all_tenants,
                  COALESCE((SELECT array_agg(rp.permission)
                              FROM role_permissions rp WHERE rp.role_id = r.id),
                           '{}') AS permissions
             FROM roles r WHERE r.key = $1""", role_key)
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"no such role '{role_key}'")
    if actor.all_tenants:
        return
    if row["all_tenants"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"'{role_key}' can see every client, which you cannot grant")
    extra = sorted(set(row["permissions"] or ()) - actor.permissions)
    if extra:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"'{role_key}' includes {', '.join(extra)}, which you do not have "
            "yourself")


@router.get("", response_model=list[UserOut])
async def list_users(
    actor: CurrentUser = Depends(manager),
    tenant_id: int | None = Query(None, description="superadmin only"),
):
    scope = tenant_scope(actor, tenant_id)
    if scope is None:
        rows = await db.pool().fetch(SELECT_USER + " ORDER BY t.name NULLS FIRST, u.email")
    else:
        rows = await db.pool().fetch(
            SELECT_USER + " WHERE u.tenant_id = $1 ORDER BY u.email", scope)
    return [UserOut(**dict(r)) for r in rows]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, actor: CurrentUser = Depends(manager)):
    await _check_assignable(actor, body.role)

    # The schema enforces this too, but failing here gives a readable message
    # instead of a raw constraint violation.
    if body.role == "superadmin":
        tenant_id = None
        if body.tenant_id is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "a superadmin does not belong to a tenant")
    else:
        tenant_id = resolve_tenant(actor, body.tenant_id)
        exists = await db.pool().fetchval(
            "SELECT 1 FROM tenants WHERE id = $1", tenant_id)
        if not exists:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")

    try:
        new_id = await db.pool().fetchval(
            """INSERT INTO users (tenant_id, email, name, password_hash, role,
                                  must_change_password, created_by)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
            tenant_id, body.email, body.name,
            security.hash_password(body.password), body.role,
            body.must_change_password, actor.id)
    except asyncpg.UniqueViolationError:
        # email uniqueness is global, not per tenant - say so plainly
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{body.email} is already in use")

    await audit.record(actor, entity="user", entity_id=new_id, action="create",
                       tenant_id=tenant_id,
                       changes=audit.diff(None, audit.safe(body.model_dump())))
    return UserOut(**await _get(new_id))


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: int, body: UserUpdate,
                      actor: CurrentUser = Depends(manager)):
    target = await _target_or_404(actor, user_id)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return UserOut(**target)

    if "role" in fields:
        await _check_assignable(actor, fields["role"])
        # Changing a role across the superadmin boundary would violate the
        # schema's tenant/role CHECK, and is not something the panel should do.
        if (fields["role"] == "superadmin") != (target["role"] == "superadmin"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "cannot move an account into or out of the superadmin role")

    # Locking yourself out of your own console is never the intent.
    if user_id == actor.id:
        if fields.get("active") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "you cannot deactivate your own account")
        if "role" in fields and fields["role"] != target["role"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "you cannot change your own role")

    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
    await db.pool().execute(
        f"UPDATE users SET {sets}, updated_at = now() WHERE id = $1",
        user_id, *fields.values())

    action = "disable" if fields.get("active") is False else "update"
    await audit.record(actor, entity="user", entity_id=user_id, action=action,
                       tenant_id=target["tenant_id"],
                       changes=audit.diff(target, fields))
    return UserOut(**await _get(user_id))


@router.post("/{user_id}/password", response_model=UserOut)
async def reset_password(user_id: int, body: PasswordReset,
                         actor: CurrentUser = Depends(manager)):
    target = await _target_or_404(actor, user_id)

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE users
                      SET password_hash = $2, must_change_password = $3,
                          password_changed_at = now(), updated_at = now()
                    WHERE id = $1""",
                user_id, security.hash_password(body.password),
                body.must_change_password)
            # Every existing session dies with the old password. Otherwise a
            # reset prompted by a suspected compromise leaves the intruder
            # signed in for up to a week on their refresh token.
            await conn.execute(
                "UPDATE user_sessions SET revoked_at = now() "
                "WHERE user_id = $1 AND revoked_at IS NULL", user_id)

    await audit.record(actor, entity="user", entity_id=user_id,
                       action="reset_password", tenant_id=target["tenant_id"])
    return UserOut(**await _get(user_id))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, actor: CurrentUser = Depends(manager)):
    target = await _target_or_404(actor, user_id)
    if user_id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "you cannot delete your own account")

    # Refuse to remove the last superadmin - there would be no way back in.
    if target["role"] == "superadmin":
        remaining = await db.pool().fetchval(
            "SELECT count(*) FROM users WHERE role = 'superadmin' AND active AND id <> $1",
            user_id)
        if remaining == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "this is the last active superadmin")

    await db.pool().execute("DELETE FROM users WHERE id = $1", user_id)
    await audit.record(actor, entity="user", entity_id=user_id, action="delete",
                       tenant_id=target["tenant_id"],
                       changes={"email": {"from": target["email"], "to": None}})
