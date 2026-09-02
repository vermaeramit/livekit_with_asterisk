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
                 request: Request,
                 last_seen_at: datetime | None = None) -> TokenPair:
    """A new session row and the pair of tokens for it.

    `last_seen_at` is CARRIED FORWARD on a rotation and defaults to now only
    for a fresh login. Letting it default on every refresh would have been the
    same bug the whole feature exists to avoid: the browser refreshes when an
    access token expires, which happens on its own every fifteen minutes while
    the layout polls, so an abandoned tab would have reset its own idle clock
    for a week.
    """
    raw, hashed = security.new_refresh_token()

    # Inserted first: the access token has to carry the row's id, so the row
    # has to exist before the token does.
    session_id = await db.pool().fetchval(
        """INSERT INTO user_sessions (user_id, refresh_hash, user_agent, ip,
                                      expires_at, last_seen_at)
           VALUES ($1, $2, $3, $4, $5, coalesce($6, now())) RETURNING id""",
        user_id, hashed,
        request.headers.get("user-agent", "")[:400],
        request.client.host if request.client else None,
        datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
        last_seen_at,
    )
    access = security.create_access_token(user_id, tenant_id, role, session_id)
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
        """SELECT s.id, s.user_id, s.expires_at, s.revoked_at, s.last_seen_at,
                  u.tenant_id, u.role, u.active
             FROM user_sessions s JOIN users u ON u.id = s.user_id
            WHERE s.refresh_hash = $1""", hashed)

    if sess is None or sess["revoked_at"] is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
    if sess["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token expired")
    if not sess["active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")

    # The idle rule, enforced here because here is the only place it CAN be
    # enforced for a browser that is gone. A closed laptop has no client left
    # to log itself out, so the server refuses the next refresh instead.
    #
    # One minute of grace, and it is not slack. The browser measures idleness
    # from the last input; the server measures it from the last HEARTBEAT, and
    # heartbeats are throttled to one a minute. Without the grace a user active
    # 59 seconds after their last beat would be refused a minute BEFORE their
    # own warning appeared - signed out with no warning at all, which is the
    # one outcome this feature was supposed to remove.
    idle_for = datetime.now(timezone.utc) - sess["last_seen_at"]
    if idle_for > timedelta(minutes=settings.idle_timeout_minutes, seconds=60):
        await db.pool().execute(
            "UPDATE user_sessions SET revoked_at = now() WHERE id = $1",
            sess["id"])
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "signed out after "
                            f"{settings.idle_timeout_minutes} minutes of "
                            "inactivity")

    # Rotate: the old refresh token dies with this request. A stolen token is
    # then usable at most once, and the legitimate client's next refresh fails
    # loudly instead of silently sharing a session with an attacker.
    await db.pool().execute(
        "UPDATE user_sessions SET revoked_at = now() WHERE id = $1", sess["id"])
    return await _issue(sess["user_id"], sess["tenant_id"], sess["role"],
                        request, last_seen_at=sess["last_seen_at"])


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, user: CurrentUser = Depends(current_user)):
    await db.pool().execute(
        "UPDATE user_sessions SET revoked_at = now() "
        "WHERE refresh_hash = $1 AND user_id = $2 AND revoked_at IS NULL",
        security.hash_refresh_token(body.refresh_token), user.id)


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(user: CurrentUser = Depends(current_user)):
    """The browser reporting that a person is actually there.

    Sent on real input only - mouse, keyboard, touch - and at most once a
    minute. Nothing else moves last_seen_at: if ordinary API traffic did, the
    layout's own 60-second poll for alert and gap counts would keep every
    abandoned tab signed in until the refresh token expired a week later.

    Deliberately cheap and deliberately quiet. It is called for the whole
    working day and has nothing to say.
    """
    if not user.session_id:
        # A token issued before sessions carried an id. It cannot report
        # activity, and will be turned away at its next refresh - which is the
        # right outcome, once, rather than an error the user cannot act on.
        return
    await db.pool().execute(
        "UPDATE user_sessions SET last_seen_at = now() "
        " WHERE id = $1 AND revoked_at IS NULL", user.session_id)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser = Depends(current_user)):
    row = await db.pool().fetchrow(
        """SELECT u.id, u.email, u.name, u.role, u.tenant_id, u.active,
                  u.must_change_password, u.last_login_at, u.created_at,
                  t.name AS tenant_name
             FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id
            WHERE u.id = $1""", user.id)
    # Resolved on the request rather than read again, so this can never disagree
    # with what the guards will actually do a moment later.
    return UserOut(**dict(row),
                   permissions=sorted(user.permissions),
                   all_tenants=user.all_tenants)


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
