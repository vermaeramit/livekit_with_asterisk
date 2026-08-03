from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db, security

bearer = HTTPBearer(auto_error=False)

ROLES = ("superadmin", "tenant_admin", "agent", "viewer")


@dataclass(frozen=True)
class CurrentUser:
    id: int
    tenant_id: int | None
    role: str
    email: str

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"


async def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    try:
        payload = security.decode_access_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")

    # Re-read the user on every request rather than trusting the token body.
    # A deactivated user, a changed role or a moved tenant then takes effect
    # immediately instead of lingering until the access token expires.
    row = await db.pool().fetchrow(
        "SELECT id, tenant_id, role, email, active FROM users WHERE id = $1",
        int(payload["sub"]),
    )
    if row is None or not row["active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user inactive")

    return CurrentUser(id=row["id"], tenant_id=row["tenant_id"],
                       role=row["role"], email=row["email"])


def require_roles(*roles: str):
    """Route guard. Superadmin passes everything."""
    for r in roles:
        assert r in ROLES, f"unknown role {r}"

    async def _guard(user: CurrentUser = Depends(current_user)) -> CurrentUser:
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


async def assert_campaign_visible(user: CurrentUser, campaign_id: int) -> None:
    row = await db.pool().fetchrow(
        "SELECT tenant_id FROM campaigns WHERE id = $1", campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found")
    if not user.is_superadmin and row["tenant_id"] != user.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "wrong tenant")
