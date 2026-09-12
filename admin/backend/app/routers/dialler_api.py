"""The one endpoint the dialler calls, and the only route here without a login.

    GET /api/dialler/capacity?campaign_id=<the dialler's own id>
    X-API-Key: <the client's key>

    -> {"activeCalls": 3, "capacity": 10, "availableSlots": 7,
        "timestamp": "2026-09-11T10:15:30.123Z"}

The dialler needs one number before placing a call: is there room. It already
knows how many it sent; what it cannot know is how many are still up. Asking is
cheaper than the hold queue, which exists to absorb exactly this guess.

The response shape is THEIRS. camelCase is not this codebase's convention and
the field names are copied from what they asked for, because a contract given to
another team is not the place to express a preference.

Everything else here is deliberately absent. No POST, no way to start or stop
anything, no call details, no phone numbers - a key handed to another team should
be able to answer one question and do nothing at all.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel

from .. import db

log = logging.getLogger("admin-api")

router = APIRouter(prefix="/dialler", tags=["dialler"])


class Capacity(BaseModel):
    # camelCase on purpose - see the module docstring. Named directly rather
    # than aliased so that what is written here is exactly what goes on the
    # wire, with nothing in between to get wrong.
    activeCalls: int
    capacity: int
    availableSlots: int
    timestamp: str


def _now() -> str:
    """`2026-09-11T10:15:30.123Z`, which is the format they asked for.

    Built by hand because nothing standard produces it: isoformat() gives
    `+00:00` rather than `Z` and six decimal places rather than three. Both
    differences are the kind of thing that parses fine in testing and then
    fails on one library somewhere at 2am.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


@router.get("/capacity", response_model=Capacity)
async def capacity(
    campaign_id: str = Query(..., min_length=1, max_length=128,
                             description="The dialler's own campaign id"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "X-API-Key header is required")

    # Looked up by hash, so the key itself never exists in this process beyond
    # this line and never reaches a log. There is no string comparison to make
    # constant-time: matching a row requires producing the hash, which requires
    # the key.
    digest = hashlib.sha256(x_api_key.encode()).hexdigest()

    row = await db.pool().fetchrow(
        """SELECT t.id            AS tenant_id,
                  t.status        AS tenant_status,
                  cam.id          AS campaign_id,
                  cam.enabled     AS campaign_enabled,
                  ac.max_parallel_calls AS capacity
             FROM tenants t
             LEFT JOIN campaign_dialler_ids d
                    ON d.dialler_campaign_id = $2
             LEFT JOIN campaigns cam
                    ON cam.id = d.campaign_id AND cam.tenant_id = t.id
             LEFT JOIN agent_config ac
                    ON ac.campaign_id = cam.id
            WHERE t.api_key_hash = $1""",
        digest, campaign_id.strip())

    if row is None:
        # The key matched no client at all.
        log.warning("dialler capacity: unknown API key (campaign_id=%r)",
                    campaign_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")

    if row["campaign_id"] is None:
        # Either the id does not exist, or it belongs to a different client.
        #
        # 404 for BOTH, and never 403. A 403 would confirm that the id is real
        # and simply not theirs, which is enough to enumerate another client's
        # campaign ids one guess at a time. The two cases are indistinguishable
        # from outside on purpose.
        log.warning("dialler capacity: campaign_id=%r not found for tenant %s",
                    campaign_id, row["tenant_id"])
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"no campaign is registered for campaign_id "
                            f"'{campaign_id}'")

    # Not taking calls at all, for a reason that has nothing to do with how many
    # are up. store.load_config_for_did already REFUSES a call on a disabled
    # campaign or a suspended client - so reporting ten free slots here would
    # have the dialler placing calls that are turned away one by one, and the
    # capacity number would look perfectly healthy throughout.
    #
    # 409 with the reason in it rather than availableSlots: 0. Zero would stop
    # the dialling, which is right, and then leave whoever asks "why has nothing
    # gone out" looking at a campaign reporting 0 active, capacity 10 and 0
    # available - three numbers that cannot all be true.
    if row["tenant_status"] != "active":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this client is suspended, so no calls are being accepted")
    if not row["campaign_enabled"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this campaign is disabled, so no calls are being accepted")

    if row["capacity"] is None:
        # Should be unreachable: a dialler id cannot be added to a campaign with
        # no limit, and the limit cannot be cleared while one exists. Answered
        # as a fault rather than as a number, because every alternative is a
        # lie - null stops a dialler that reads it as zero, and a large number
        # keeps it dialling into calls that will be refused.
        log.error("dialler capacity: campaign %s has dialler ids but no "
                  "max_parallel_calls - the guard that prevents this has been "
                  "bypassed", row["campaign_id"])
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this campaign has no concurrent call limit configured, so its "
            "capacity cannot be reported")

    active = await db.pool().fetchval(
        "SELECT count(*) FROM calls WHERE campaign_id = $1 AND ended_at IS NULL",
        row["campaign_id"])

    cap = int(row["capacity"])
    active = int(active or 0)
    return Capacity(
        activeCalls=active,
        capacity=cap,
        # Never negative. active can exceed capacity - a limit lowered while
        # calls are up does exactly that - and a dialler subtracting its own way
        # to -2 is one that has to guard for it. Clamped here instead.
        availableSlots=max(0, cap - active),
        timestamp=_now(),
    )
