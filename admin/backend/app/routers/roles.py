"""The role master, and what each role may do.

Platform-wide. A client assigns its people to these roles and cannot edit the
roles themselves - which is the difference between a support request and a
client locking itself out of its own console.

Everything here is guarded by `users.manage`, EXCEPT writes, which additionally
require all_tenants. Reading the list has to be open to anyone who assigns
users, or the user form has nothing to offer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, db
from ..deps import CurrentUser, require_all_tenants, require_perm
from ..permissions import GROUPS, PERMISSIONS, valid
from ..schemas import PermissionOut, RoleIn, RoleOut

router = APIRouter(tags=["roles"])

assigner = require_perm("users.manage")
platform = require_all_tenants()

_SELECT = """
    SELECT r.id, r.key, r.name, r.description, r.all_tenants, r.builtin,
           r.updated_at,
           COALESCE((SELECT array_agg(rp.permission ORDER BY rp.permission)
                       FROM role_permissions rp WHERE rp.role_id = r.id),
                    '{}') AS permissions,
           (SELECT count(*) FROM users u WHERE u.role_id = r.id) AS user_count
      FROM roles r
"""


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(user: CurrentUser = Depends(assigner)):
    """Every permission this build enforces, for the roles page to draw.

    Served from the backend rather than duplicated in the console, so the page
    can never offer a permission that guards nothing.
    """
    return [PermissionOut(key=k, group=g, label=lbl, description=desc)
            for k, (g, lbl, desc) in PERMISSIONS.items()]


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(user: CurrentUser = Depends(assigner)):
    rows = await db.pool().fetch(_SELECT + " ORDER BY r.builtin DESC, r.name")
    out = [RoleOut(**dict(r)) for r in rows]
    if user.all_tenants:
        return out
    # A tenant admin sees only what it could actually hand out. Listing a role
    # it cannot assign is an offer the save would refuse.
    return [r for r in out if _assignable_by(user, r)]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(body: RoleIn, actor: CurrentUser = Depends(platform)):
    if body.key in ("superadmin",):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "that key is reserved")
    perms = valid(body.permissions)
    async with db.pool().acquire() as con, con.transaction():
        row = await con.fetchrow(
            """INSERT INTO roles (key, name, description, all_tenants)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            body.key, body.name, body.description, body.all_tenants)
        await _set_permissions(con, row["id"], perms)
    await audit.record(actor, entity="role", entity_id=body.key, action="create",
                       changes={"permissions": {"from": [], "to": perms}})
    return await _one(row["id"])


@router.put("/roles/{role_id}", response_model=RoleOut)
async def update_role(role_id: int, body: RoleIn,
                      actor: CurrentUser = Depends(platform)):
    """Change a role's name and what it may do.

    A built-in role is refused outright. It holds every permission and every
    other guard in the system assumes something does; letting it be edited means
    one wrong save closes the console with nothing left that can reopen it.
    """
    row = await db.pool().fetchrow(
        "SELECT key, builtin FROM roles WHERE id = $1", role_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such role")
    if row["builtin"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{row['key']}' is built in and cannot be changed. Copy it into a "
            "new role instead.")

    before = await _one(role_id)
    perms = valid(body.permissions)

    # You cannot take away your own way back in. Locking yourself out of user
    # management is not recoverable from inside the console.
    if (actor.role == row["key"] and "users.manage" not in perms):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "that would remove your own ability to manage users, and this is "
            "the role you are signed in with")

    async with db.pool().acquire() as con, con.transaction():
        await con.execute(
            """UPDATE roles SET name = $2, description = $3, all_tenants = $4,
                                updated_at = now()
                WHERE id = $1""",
            role_id, body.name, body.description, body.all_tenants)
        await _set_permissions(con, role_id, perms)

    await audit.record(actor, entity="role", entity_id=row["key"], action="update",
                       changes={"permissions": {"from": before.permissions,
                                                "to": perms}})
    return await _one(role_id)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(role_id: int, actor: CurrentUser = Depends(platform)):
    row = await db.pool().fetchrow(
        """SELECT r.key, r.builtin,
                  (SELECT count(*) FROM users u WHERE u.role_id = r.id) AS users
             FROM roles r WHERE r.id = $1""", role_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such role")
    if row["builtin"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "a built-in role cannot be deleted")
    if row["users"]:
        # The foreign key would refuse this anyway; saying who is using it is
        # the difference between an error and something actionable.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{row['users']} user(s) still have this role. Move them to another "
            "role first.")
    await db.pool().execute("DELETE FROM roles WHERE id = $1", role_id)
    await audit.record(actor, entity="role", entity_id=row["key"],
                       action="delete")


# ───────────────────────────── helpers ─────────────────────────────

def _assignable_by(actor: CurrentUser, role: RoleOut) -> bool:
    """Can this actor hand out this role?

    You cannot grant what you do not hold. Without that rule a tenant admin
    could mint a role carrying `tenants.manage` and hand it to itself, which is
    a privilege escalation in two clicks.
    """
    if actor.all_tenants:
        return True
    if role.all_tenants:
        return False
    return set(role.permissions).issubset(actor.permissions)


async def _set_permissions(con, role_id: int, perms: list[str]) -> None:
    await con.execute("DELETE FROM role_permissions WHERE role_id = $1", role_id)
    if perms:
        await con.executemany(
            "INSERT INTO role_permissions (role_id, permission) VALUES ($1, $2)",
            [(role_id, p) for p in perms])


async def _one(role_id: int) -> RoleOut:
    row = await db.pool().fetchrow(_SELECT + " WHERE r.id = $1", role_id)
    return RoleOut(**dict(row))


__all__ = ["router", "GROUPS"]
