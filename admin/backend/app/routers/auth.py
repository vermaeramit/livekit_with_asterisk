from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from .. import audit, db, security
from ..config import settings
from ..deps import CurrentUser, current_user
from ..schemas import (ChangePasswordRequest, LoginRequest, RefreshRequest,
                       TokenPair, UserOut)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _issue(user_id: int, tenant_id: int | None, role: str,
                 request: Request) -> TokenPair:
    access = security.create_access_token(user_id, tenant_id, role)
    raw, hashed = security.new_refresh_token()

    await db.pool().execute(
        """INSERT INTO user_sessions (user_id, refresh_hash, user_agent, ip, expires_at)
           VALUES ($1, $2, $3, $4, $5)""",
        user_id, hashed,
        request.headers.get("user-agent", "")[:400],
        request.client.host if request.client else None,
        datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
    )
    return TokenPair(access_token=access, refresh_token=raw,
                     expires_in=settings.access_token_minutes * 60)


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, request: Request):
    row = await db.pool().fetchrow(
        """SELECT u.id, u.tenant_id, u.role, u.email, u.password_hash, u.active,
                  COALESCE(t.status, 'active') AS tenant_status
             FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id
            WHERE lower(u.email) = lower($1)""", body.email)

    # Verify against a dummy hash when the user is missing so a wrong email and a
    # wrong password take the same time. Otherwise the response time enumerates
    # valid accounts.
    if row is None:
        security.verify_password(body.password, security.hash_password("x"))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    if not security.verify_password(body.password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    if not row["active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")
    if row["tenant_status"] == "suspended":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this account is suspended")

    if security.needs_rehash(row["password_hash"]):
        await db.pool().execute(
            "UPDATE users SET password_hash = $2 WHERE id = $1",
            row["id"], security.hash_password(body.password))

    await db.pool().execute(
        "UPDATE users SET last_login_at = now() WHERE id = $1", row["id"])
    return await _issue(row["id"], row["tenant_id"], row["role"], request)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, request: Request):
    hashed = security.hash_refresh_token(body.refresh_token)
    sess = await db.pool().fetchrow(
        """SELECT s.id, s.user_id, s.expires_at, s.revoked_at,
                  u.tenant_id, u.role, u.active
             FROM user_sessions s JOIN users u ON u.id = s.user_id
            WHERE s.refresh_hash = $1""", hashed)

    if sess is None or sess["revoked_at"] is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
    if sess["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token expired")
    if not sess["active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")

    # Rotate: the old refresh token dies with this request. A stolen token is
    # then usable at most once, and the legitimate client's next refresh fails
    # loudly instead of silently sharing a session with an attacker.
    await db.pool().execute(
        "UPDATE user_sessions SET revoked_at = now() WHERE id = $1", sess["id"])
    return await _issue(sess["user_id"], sess["tenant_id"], sess["role"], request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, user: CurrentUser = Depends(current_user)):
    await db.pool().execute(
        "UPDATE user_sessions SET revoked_at = now() "
        "WHERE refresh_hash = $1 AND user_id = $2 AND revoked_at IS NULL",
        security.hash_refresh_token(body.refresh_token), user.id)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser = Depends(current_user)):
    row = await db.pool().fetchrow(
        """SELECT u.id, u.email, u.name, u.role, u.tenant_id, u.active,
                  u.must_change_password, u.last_login_at, u.created_at,
                  t.name AS tenant_name
             FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id
            WHERE u.id = $1""", user.id)
    return UserOut(**dict(row))


@router.post("/change-password", response_model=TokenPair)
async def change_password(body: ChangePasswordRequest, request: Request,
                          user: CurrentUser = Depends(current_user)):
    """Change your own password.

    Depends on `current_user`, not `active_user` - a user who has been told to
    change their password must be able to reach exactly this one endpoint.
    """
    row = await db.pool().fetchrow(
        "SELECT password_hash FROM users WHERE id = $1", user.id)
    if not security.verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "your current password is incorrect")
    if body.current_password == body.new_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "the new password must be different")

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE users
                      SET password_hash = $2, must_change_password = false,
                          password_changed_at = now(), updated_at = now()
                    WHERE id = $1""",
                user.id, security.hash_password(body.new_password))
            # Sign out everywhere else. If the password was changed because it
            # may have leaked, leaving other sessions alive defeats the point.
            await conn.execute(
                "UPDATE user_sessions SET revoked_at = now() "
                "WHERE user_id = $1 AND revoked_at IS NULL", user.id)

    await audit.record(user, entity="user", entity_id=user.id,
                       action="change_password")
    # The caller's own session was just revoked with the rest, so hand back a
    # fresh pair rather than bouncing them to the login screen.
    return await _issue(user.id, user.tenant_id, user.role, request)
