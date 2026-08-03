from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db, security

bearer = HTTPBearer(auto_error=False)

ROLES = ("superadmin", "tenant_admin", "agent", "viewer")

# Roles a tenant_admin is allowed to hand out. It can create peers inside its own
# tenant but can never mint a superadmin - that would be a privilege escalation
# with one API call.
TENANT_ASSIGNABLE_ROLES = ("tenant_admin", "agent", "viewer")


@dataclass(frozen=True)
class CurrentUser:
    id: int
    tenant_id: int | None
    role: str
    email: str
    must_change_password: bool = False

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"


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
        """SELECT u.id, u.tenant_id, u.role, u.email, u.active,
                  u.must_change_password,
                  COALESCE(t.status, 'active') AS tenant_status
             FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id
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


def require_roles(*roles: str):
    """Route guard. Superadmin passes everything."""
    for r in roles:
        assert r in ROLES, f"unknown role {r}"

    async def _guard(user: CurrentUser = Depends(active_user)) -> CurrentUser:
        if user.is_superadmin or user.role in roles:
            return user
        raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient permissions")

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
