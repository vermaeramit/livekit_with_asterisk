"""The diallers a campaign can hand a call to.

Platform-level, like provider rates, and for the same reason: a row here can
dial a trunk, so it is not a tenant's to write.

The credentials used to live in iax.conf and now live here - see migration 034
for what that costs. Asterisk reads them over ODBC as a realtime peer, so a
dialler added in the console is dialable on the next transfer with no reload
and no server access at all.

The secret is never returned, never logged, and never appears in the audit
trail - only whether one is set. It cannot be encrypted at rest: IAX2 is MD5
challenge-response and the thing that reads it is Asterisk, which has no key.

Anyone with campaign.write can READ the list, or the campaign form has nothing
to choose from. Only somebody who sees every client can add or change one,
because `peer` has to match a section in iax.conf that only they can create.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, db
from ..deps import CurrentUser, require_all_tenants, require_perm
from ..schemas import DiallerIn, DiallerOut

router = APIRouter(tags=["diallers"])

reader = require_perm("campaign.write")
platform = require_all_tenants()

# d.secret is deliberately not selected. Not masked in the router, not dropped
# by the response model - not read. A column that is never loaded cannot be
# leaked by a later change to either of those.
_SELECT = """
    SELECT d.id, d.name, d.peer, d.description, d.active, d.updated_at,
           d.host, d.port, d.username,
           (d.secret IS NOT NULL AND d.secret <> '') AS has_secret,
           (SELECT count(*) FROM agent_config ac
             WHERE ac.transfer_dialler_id = d.id) AS campaign_count
      FROM diallers d
"""


@router.get("/diallers", response_model=list[DiallerOut])
async def list_diallers(user: CurrentUser = Depends(reader)):
    rows = await db.pool().fetch(_SELECT + " ORDER BY d.active DESC, d.name")
    return [DiallerOut(**dict(r)) for r in rows]


@router.post("/diallers", response_model=DiallerOut,
             status_code=status.HTTP_201_CREATED)
async def create_dialler(body: DiallerIn,
                         actor: CurrentUser = Depends(platform)):
    if body.host and not body.secret:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "a dialler with a host needs a password too - without one Asterisk "
            "builds a peer that cannot authenticate, which looks like the "
            "dialler being down")
    row = await db.pool().fetchrow(
        """INSERT INTO diallers (name, peer, description, active,
                                 host, port, username, secret)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id""",
        body.name, body.peer, body.description, body.active,
        body.host, body.port, body.username, body.secret)
    await audit.record(actor, entity="dialler", entity_id=body.peer,
                       action="create",
                       changes=_changes(None, body))
    return await _one(row["id"])


@router.put("/diallers/{dialler_id}", response_model=DiallerOut)
async def update_dialler(dialler_id: int, body: DiallerIn,
                         actor: CurrentUser = Depends(platform)):
    before = await db.pool().fetchrow(
        """SELECT peer, active, host, port, username,
                  (secret IS NOT NULL AND secret <> '') AS has_secret
             FROM diallers WHERE id = $1""", dialler_id)
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such dialler")

    if body.host and not body.secret and not before["has_secret"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "a dialler with a host needs a password too - without one Asterisk "
            "builds a peer that cannot authenticate, which looks like the "
            "dialler being down")

    # An absent secret means unchanged, because the form never received it and
    # so cannot send it back. Clearing one is done by clearing the host, which
    # is what turns the dialler back into a name for a hand-written peer.
    if body.secret:
        await db.pool().execute("UPDATE diallers SET secret = $2 WHERE id = $1",
                                dialler_id, body.secret)
    elif not body.host:
        await db.pool().execute("UPDATE diallers SET secret = NULL "
                                " WHERE id = $1", dialler_id)

    await db.pool().execute(
        """UPDATE diallers SET name = $2, peer = $3, description = $4,
                               active = $5, host = $6, port = $7,
                               username = $8, updated_at = now()
            WHERE id = $1""",
        dialler_id, body.name, body.peer, body.description, body.active,
        body.host, body.port, body.username)
    await audit.record(actor, entity="dialler", entity_id=body.peer,
                       action="update", changes=_changes(before, body))
    return await _one(dialler_id)


@router.delete("/diallers/{dialler_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dialler(dialler_id: int,
                         actor: CurrentUser = Depends(platform)):
    """Refused while a campaign still points at it.

    The foreign key is ON DELETE SET NULL, so this would otherwise succeed
    quietly and leave those campaigns unable to transfer - discovered by a
    caller asking for a person. Naming the count makes it a job instead.
    """
    row = await db.pool().fetchrow(
        """SELECT d.peer,
                  (SELECT count(*) FROM agent_config ac
                    WHERE ac.transfer_dialler_id = d.id) AS used
             FROM diallers d WHERE d.id = $1""", dialler_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such dialler")
    if row["used"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{row['used']} campaign(s) still transfer to this dialler. Point "
            "them somewhere else first, or set it inactive to stop new use "
            "without breaking them.")
    await db.pool().execute("DELETE FROM diallers WHERE id = $1", dialler_id)
    await audit.record(actor, entity="dialler", entity_id=row["peer"],
                       action="delete")


def _changes(before, body: DiallerIn) -> dict:
    """What the audit trail records.

    Everything except the secret, and for that only whether one was given -
    an audit row that carries the password would defeat the point of never
    returning it, and audit rows are read by more people than this endpoint is.
    """
    was = dict(before) if before is not None else {}
    out: dict = {}
    for field in ("peer", "active", "host", "port", "username"):
        new_value = getattr(body, field)
        old_value = was.get(field)
        if old_value != new_value:
            out[field] = {"from": old_value, "to": new_value}
    if body.secret:
        out["secret"] = {"from": None, "to": "changed"}
    return out


async def _one(dialler_id: int) -> DiallerOut:
    row = await db.pool().fetchrow(_SELECT + " WHERE d.id = $1", dialler_id)
    return DiallerOut(**dict(row))
