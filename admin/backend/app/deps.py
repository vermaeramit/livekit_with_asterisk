from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db, security

bearer = HTTPBearer(auto_error=False)

# The roles that ship. Roles are rows now - see migration 030 - so this is only
# the seeded set, kept for anything that still validates a name against it.
#
# What a role may hand out is no longer a list of names. A tenant admin could be
# given a role called "desk" carrying tenants.manage, and a name tells you
# nothing about that; users.py compares permissions instead.
ROLES = ("superadmin", "tenant_admin", "agent", "viewer")


@dataclass(frozen=True)
class CurrentUser:
    id: int
    tenant_id: int | None
    role: str
    email: str
    must_change_password: bool = False
    # What this user's role allows, resolved on every request. See
    # permissions.py for the list and migration 030 for where it is stored.
    permissions: frozenset[str] = frozenset()
    # Sees every client. Held apart from `permissions` on purpose: "which
    # tenants" is a different question from "may do what", and a role that could
    # grant itself the whole platform by ticking a permission would be a
    # privilege escalation dressed up as a checkbox.
    all_tenants: bool = False

    @property
    def is_superadmin(self) -> bool:
        """Reads across every client.

        Kept as the name the rest of the code already uses. It now means
        all_tenants rather than a role called "superadmin" - the two came apart
        the moment roles became data somebody can edit.
        """
        return self.all_tenants

    def can(self, permission: str) -> bool:
        return permission in self.permissions


async def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser:
    """Identity only.

    Use this for the handful of endpoints a half-onboarded user must still reach
    (`/auth/me`, `/auth/change-password`, `/auth/logout`). Everything else should
    depend on `active_user`.
    """
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    try:
        payload = security.decode_access_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")

    # Re-read the user on every request rather than trusting the token body.
    # A deactivated user, a changed role, a moved tenant or a suspended client
    # then takes effect immediately instead of lingering until the token expires.
    row = await db.pool().fetchrow(
        """SELECT u.id, u.tenant_id, u.email, u.active,
                  u.must_change_password,
                  COALESCE(t.status, 'active') AS tenant_status,
                  COALESCE(r.key, u.role)  AS role,
                  COALESCE(r.all_tenants, u.role = 'superadmin') AS all_tenants,
                  COALESCE(
                      (SELECT array_agg(rp.permission)
                         FROM role_permissions rp WHERE rp.role_id = r.id),
                      '{}') AS permissions
             FROM users u
             LEFT JOIN tenants t ON t.id = u.tenant_id
             LEFT JOIN roles   r ON r.id = u.role_id
            WHERE u.id = $1""",
        int(payload["sub"]),
    )
    if row is None or not row["active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user inactive")
    if row["tenant_status"] == "suspended":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this account is suspended")

    return CurrentUser(
        id=row["id"], tenant_id=row["tenant_id"], role=row["role"],
        email=row["email"], must_change_password=row["must_change_password"],
        permissions=frozenset(row["permissions"] or ()),
        all_tenants=bool(row["all_tenants"]),
    )


async def active_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """A fully onboarded user. This is what data endpoints should depend on.

    Enforcing the password change here rather than in the UI matters: an admin
    picks the initial password, so until it is replaced the admin can sign in as
    that user. A client-side redirect is a suggestion, not a control.
    """
    if user.must_change_password:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "password change required before using the console",
        )
    return user


def require_perm(*permissions: str):
    """Route guard. Every named permission is required, not any of them.

    Asserted against the known list at import time rather than at request time:
    a typo in a guard would otherwise be a route nobody can reach, discovered by
    a user rather than by starting the process.

    all_tenants does NOT imply everything. The superadmin role holds every
    permission because migration 030 gives it every permission, which is a fact
    in the database somebody could look at - not a branch in here that no
    permission list can describe.
    """
    from .permissions import PERMISSIONS
    for p in permissions:
        assert p in PERMISSIONS, f"unknown permission {p}"

    async def _guard(user: CurrentUser = Depends(active_user)) -> CurrentUser:
        missing = [p for p in permissions if p not in user.permissions]
        if missing:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"your role does not allow this ({', '.join(missing)})")
        return user

    return _guard


def require_all_tenants():
    """Guard for the handful of things that are about the platform itself."""
    async def _guard(user: CurrentUser = Depends(active_user)) -> CurrentUser:
        if not user.all_tenants:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "this is a platform-wide setting")
        return user

    return _guard


def tenant_scope(user: CurrentUser, requested: int | None = None) -> int | None:
    """Resolve which tenant a request may read, and refuse anything else.

    Tenant isolation is a security boundary, not a convenience filter - so it is
    resolved here, once, and every query takes its answer. A non-superadmin can
    never widen its own scope, whatever it puts in the query string.

    -> tenant id to filter by, or None meaning "all tenants" (superadmin only)
    """
    if user.is_superadmin:
        return requested            # None = every tenant
    if requested is not None and requested != user.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "wrong tenant")
    return user.tenant_id


def resolve_tenant(user: CurrentUser, requested: int | None) -> int:
    """Which tenant a *write* lands in. Unlike tenant_scope, there is no 'all'."""
    if user.is_superadmin:
        if requested is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "tenant_id is required for a superadmin")
        return requested
    if requested is not None and requested != user.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "wrong tenant")
    assert user.tenant_id is not None      # guaranteed by the schema CHECK
    return user.tenant_id


async def assert_campaign_visible(user: CurrentUser, campaign_id: int) -> int:
    """-> the campaign's tenant_id, or 404/403."""
    row = await db.pool().fetchrow(
        "SELECT tenant_id FROM campaigns WHERE id = $1", campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found")
    if not user.is_superadmin and row["tenant_id"] != user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found")
    return row["tenant_id"]
