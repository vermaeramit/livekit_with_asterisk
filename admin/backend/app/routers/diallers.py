"""The diallers a campaign can hand a call to.

Platform-level, like provider rates. A dialler is half a row here and half a
peer in iax.conf: the credentials live on the server because they are
infrastructure and because a console that could write them would be a console
that could dial anywhere. This half is the part that changes often.

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

_SELECT = """
    SELECT d.id, d.name, d.peer, d.description, d.active, d.updated_at,
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
    row = await db.pool().fetchrow(
        """INSERT INTO diallers (name, peer, description, active)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        body.name, body.peer, body.description, body.active)
    await audit.record(actor, entity="dialler", entity_id=body.peer,
                       action="create",
                       changes={"peer": {"from": None, "to": body.peer}})
    return await _one(row["id"])


@router.put("/diallers/{dialler_id}", response_model=DiallerOut)
async def update_dialler(dialler_id: int, body: DiallerIn,
                         actor: CurrentUser = Depends(platform)):
    before = await db.pool().fetchrow(
        "SELECT peer, active FROM diallers WHERE id = $1", dialler_id)
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such dialler")

    await db.pool().execute(
        """UPDATE diallers SET name = $2, peer = $3, description = $4,
                               active = $5, updated_at = now()
            WHERE id = $1""",
        dialler_id, body.name, body.peer, body.description, body.active)
    await audit.record(actor, entity="dialler", entity_id=body.peer,
                       action="update",
                       changes={"peer": {"from": before["peer"], "to": body.peer},
                                "active": {"from": before["active"],
                                           "to": body.active}})
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


async def _one(dialler_id: int) -> DiallerOut:
    row = await db.pool().fetchrow(_SELECT + " WHERE d.id = $1", dialler_id)
    return DiallerOut(**dict(row))
