"""Provider prices, and the exchange rate they are shown through.

Superadmin only. These are platform economics: a tenant admin cannot act on
them, and a wrong number here misprices every call on the system rather than one
campaign's.

Nothing is seeded. Every price a provider charges changes on their schedule and
not ours, and a figure baked into a deployment goes stale silently - which for
money is the worst way to be wrong. A blank asks to be filled; a stale number
asks nothing.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, db
from ..deps import CurrentUser, require_roles
from ..schemas import PlatformSetting, ProviderRateIn, ProviderRateOut

router = APIRouter(tags=["rates"])

superadmin = require_roles()

_SELECT = """
    SELECT r.id, r.provider, r.model, r.kind, r.unit, r.price, r.currency, r.note,
           r.updated_at, u.email AS updated_by_email
      FROM provider_rates r
      LEFT JOIN users u ON u.id = r.updated_by
     ORDER BY r.provider, coalesce(r.model, ''), r.kind
"""


async def load_rates() -> list[dict]:
    """Every rate, as plain dicts for costing. Cheap: the table is tiny."""
    rows = await db.pool().fetch(_SELECT)
    return [dict(r) for r in rows]


async def usd_to_inr() -> Decimal | None:
    """The exchange rate, or None if nobody has set one.

    None means the console shows USD alone rather than inventing a conversion.
    A made-up rupee figure would be acted on exactly as readily as a real one.
    """
    v = await db.pool().fetchval(
        "SELECT value FROM platform_settings WHERE key = 'usd_to_inr'")
    if not v:
        return None
    try:
        rate = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return rate if rate > 0 else None


@router.get("/rates", response_model=list[ProviderRateOut])
async def list_rates(user: CurrentUser = Depends(superadmin)):
    return [ProviderRateOut(**r) for r in await load_rates()]


@router.put("/rates", response_model=ProviderRateOut)
async def upsert_rate(body: ProviderRateIn,
                      actor: CurrentUser = Depends(superadmin)):
    """One price. Replaces the existing one for the same provider/model/kind.

    An upsert rather than create-then-edit because there is only ever one right
    answer per combination, and two rows for it would mean the cost of a call
    depended on which the query happened to find first.
    """
    row = await db.pool().fetchrow(
        """INSERT INTO provider_rates
               (provider, model, kind, unit, price, currency, note, updated_by)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (provider, coalesce(model, ''), kind) DO UPDATE
               SET unit = EXCLUDED.unit,
                   price = EXCLUDED.price,
                   currency = EXCLUDED.currency,
                   note = EXCLUDED.note,
                   updated_at = now(),
                   updated_by = EXCLUDED.updated_by
           RETURNING id""",
        body.provider, body.model, body.kind, body.unit,
        body.price, body.currency, body.note, actor.id)

    await audit.record(actor, entity="provider_rate",
                       entity_id=f"{body.provider}/{body.model or '*'}/{body.kind}",
                       action="set",
                       changes={"price": {"from": None,
                                          "to": f"{body.price} {body.currency} {body.unit}"}})
    out = await db.pool().fetchrow(
        _SELECT.replace("ORDER BY", "WHERE r.id = $1 ORDER BY"), row["id"])
    return ProviderRateOut(**dict(out))


@router.delete("/rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rate(rate_id: int, actor: CurrentUser = Depends(superadmin)):
    row = await db.pool().fetchrow(
        "DELETE FROM provider_rates WHERE id = $1 "
        "RETURNING provider, model, kind", rate_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such rate")
    await audit.record(actor, entity="provider_rate",
                       entity_id=f"{row['provider']}/{row['model'] or '*'}/{row['kind']}",
                       action="delete")


@router.get("/rates/exchange")
async def get_exchange(user: CurrentUser = Depends(superadmin)):
    rate = await usd_to_inr()
    return {"usd_to_inr": float(rate) if rate else None}


@router.put("/rates/exchange")
async def set_exchange(body: PlatformSetting,
                       actor: CurrentUser = Depends(superadmin)):
    """The USD to INR rate used to show costs in rupees.

    Held rather than fetched. A live rate would make the same call cost a
    different amount every time it was looked at, and nobody reconciling a
    month's spend wants a figure that moves while they read it.
    """
    await db.pool().execute(
        """INSERT INTO platform_settings (key, value, updated_by)
           VALUES ('usd_to_inr', $1, $2)
           ON CONFLICT (key) DO UPDATE
               SET value = EXCLUDED.value, updated_at = now(),
                   updated_by = EXCLUDED.updated_by""",
        str(body.value), actor.id)
    await audit.record(actor, entity="platform_setting", entity_id="usd_to_inr",
                       action="set",
                       changes={"value": {"from": None, "to": str(body.value)}})
    return {"usd_to_inr": float(body.value)}
