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

import asyncio
import datetime
import json
import logging
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, db
from ..deps import CurrentUser, require_perm
from ..schemas import (PlatformSetting, ProviderRateIn, ProviderRateOut,
                       RateImport)

log = logging.getLogger("admin-api")
router = APIRouter(tags=["rates"])

superadmin = require_perm("rates.manage")

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


# Public - no key, no auth. Which is the whole reason this import is clean:
# prices are the same for everybody, so there is no need to borrow one client's
# credentials to read another's.
_OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"


def _fetch_openrouter_prices() -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    """-> {model id: (input, output, cached read) per million}."""
    req = urllib.request.Request(
        _OPENROUTER_MODELS,
        headers={"User-Agent": "AIVoice-Console/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8", "replace")).get("data") or []

    out: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for m in data:
        mid = m.get("id")
        pricing = m.get("pricing") or {}
        try:
            # OpenRouter quotes per TOKEN, as a string. This table stores
            # per_million, which is how every provider's page reads and what
            # somebody checking the number will be comparing against.
            prompt = Decimal(str(pricing["prompt"])) * 1_000_000
            completion = Decimal(str(pricing["completion"])) * 1_000_000
        except (KeyError, TypeError, ArithmeticError):
            continue

        # CACHED READS. OpenRouter does cache - its own docs put the discount
        # between 0.1x and 0.5x depending on who serves the model - but most
        # entries publish no field for it. gemma-4-26b-a4b-it lists exactly
        # "prompt" and "completion" and nothing else.
        #
        # Where there is a price, use it. Where there is not, charge cached
        # tokens at the FULL input rate. That overstates by whatever the real
        # discount is, and overstating is the right direction: a cost that
        # looks too high gets checked, and one that looks too low does not.
        #
        # The alternative was leaving it unpriced, which is what this import
        # did first - and call 582 then reported 10 paise for a call that cost
        # about 20, because 26,112 of its 47,329 prompt tokens were cached and
        # therefore free.
        try:
            cached = Decimal(str(pricing["input_cache_read"])) * 1_000_000
        except (KeyError, TypeError, ArithmeticError):
            cached = prompt
        out[mid] = (prompt, completion, cached)
    return out


@router.post("/rates/import/openrouter", response_model=RateImport)
async def import_openrouter_rates(actor: CurrentUser = Depends(superadmin)):
    """Take OpenRouter's own prices for the models this system actually uses.

    Typing these by hand does not scale past one model and does not stay
    correct past one price change - and a stale price here misprices every call
    on the system rather than one campaign's, which is what the module docstring
    above is about.

    Only models IN USE, not the whole catalogue: several hundred rows nobody
    reads would bury the handful that are load-bearing. In use means configured
    on a campaign now, OR having served a call already - history has to stay
    priceable after somebody switches model.
    """
    rows = await db.pool().fetch("""
        SELECT DISTINCT m FROM (
            SELECT llm_model AS m FROM agent_config
             WHERE llm_provider = 'openrouter'
            UNION
            SELECT llm_fallback_model FROM agent_config
             WHERE llm_fallback_provider = 'openrouter'
            UNION
            SELECT llm_model_used FROM calls
             WHERE llm_provider_used LIKE 'openrouter%'
        ) t WHERE m IS NOT NULL AND m <> ''""")
    wanted = [r["m"] for r in rows]
    if not wanted:
        return RateImport(written=[], missing=[],
                          note="No campaign or call uses an OpenRouter model yet.")

    try:
        prices = await asyncio.to_thread(_fetch_openrouter_prices)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"could not read OpenRouter's prices: {type(e).__name__}")

    today = datetime.date.today().isoformat()
    written, missing = [], []
    for model in sorted(wanted):
        if model not in prices:
            # Named rather than skipped. A model the catalogue does not list is
            # a model that will be priced at zero, and a zero in a spend column
            # is indistinguishable from a cheap call.
            missing.append(model)
            continue
        pin, pout, pcached = prices[model]
        for kind, price in (("llm_input", pin), ("llm_output", pout),
                            ("llm_cached", pcached)):
            await db.pool().execute(
                """INSERT INTO provider_rates
                       (provider, model, kind, unit, price, currency, note, updated_by)
                   VALUES ('openrouter', $1, $2, 'per_million', $3, 'USD', $4, $5)
                   ON CONFLICT (provider, coalesce(model, ''), kind) DO UPDATE
                       SET unit = EXCLUDED.unit, price = EXCLUDED.price,
                           currency = EXCLUDED.currency, note = EXCLUDED.note,
                           updated_at = now(), updated_by = EXCLUDED.updated_by""",
                model, kind, price, f"From OpenRouter, {today}", actor.id)
        written.append(model)

    await audit.record(actor, entity="provider_rate", entity_id="openrouter",
                       action="import",
                       changes={"models": {"from": None, "to": ", ".join(written)}})
    return RateImport(
        written=written, missing=missing,
        note=f"Prices read from OpenRouter on {today}. Where a model publishes "
             f"no cached-read price, cached tokens are charged at the full "
             f"input rate - which overstates rather than under.")


_BILLING = "https://cloudbilling.googleapis.com/v1"

# SKU description -> the voice family this table prices by. Matched explicitly
# rather than parsed, because a wrong match here misprices every Google call on
# the system, and Google's own wording is not uniform: "Count of characters for
# Chirp3-HD voices" beside "Count of characters for using wavenet voices".
#
# The keys are matched against a lowercased description, and only on SKUs whose
# description begins with "count of characters" - which leaves out the Gemini
# per-token SKUs, the on-device ones and Custom Voice, none of which this table
# can express. "Chirp Voice Cloning" contains "chirp" and deliberately matches
# nothing here; it is a feature, not a family in voices.list.
#
# Family names are spelled exactly as _google_family in routers/provider_keys.py
# derives them from a voice name, because that is what lands in tts_model and
# what costing joins on.
_GOOGLE_SKU_FAMILY = {
    "chirp3-hd": "Chirp3-HD",
    "neural2": "Neural2",
    "studio": "Studio",
    "wavenet": "Wavenet",
    "standard": "Standard",
}


def _fetch_google_tts_prices(token: str) -> dict[str, Decimal]:
    """-> {voice family: USD per million characters}, from Google's own catalogue.

    Google quotes these per single character, as units + nanos. Per million is
    what this table stores and what the pricing page shows, so the conversion
    happens here rather than in somebody's head.
    """
    def get(url: str) -> dict:
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}",
                          "User-Agent": "AIVoice-Console/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    services = get(f"{_BILLING}/services?pageSize=500").get("services") or []
    svc = next((s for s in services
                if "text-to-speech" in (s.get("displayName") or "").lower()), None)
    if svc is None:
        raise RuntimeError("no Text-to-Speech service in Google's catalogue")

    out: dict[str, Decimal] = {}
    for sku in get(f"{_BILLING}/{svc['name']}/skus?pageSize=500").get("skus") or []:
        desc = (sku.get("description") or "").lower()
        if not desc.startswith("count of characters"):
            continue
        family = next((f for k, f in _GOOGLE_SKU_FAMILY.items() if k in desc), None)
        if family is None:
            continue
        for info in sku.get("pricingInfo") or []:
            for tier in (info.get("pricingExpression") or {}).get("tieredRates") or []:
                unit = tier.get("unitPrice") or {}
                per_char = (Decimal(str(unit.get("units") or 0))
                            + Decimal(str(unit.get("nanos") or 0)) / 1_000_000_000)
                # A zero tier is the free allowance, not a price. Taking it
                # would price every call at nothing and look like a bargain.
                if per_char > 0:
                    out.setdefault(family, per_char * 1_000_000)
    return out


@router.post("/rates/import/google", response_model=RateImport)
async def import_google_rates(actor: CurrentUser = Depends(superadmin)):
    """Take Google's own published prices for Cloud Text-to-Speech.

    The same argument as the OpenRouter importer above, with one difference
    worth stating: Google prices by VOICE FAMILY, four-fold apart end to end -
    $4 per million characters for Standard against $160 for Studio. A single
    rate for "google" is not a rounding error, it is a wrong answer, so the
    family goes in the model column and the tts-catalog endpoint puts the same
    family in tts_model when a campaign picks a voice.

    The credential is the service account already stored for google. The
    project needs the Cloud Billing API enabled; without it Google answers 403
    and says so, and that message is passed through rather than swallowed.
    """
    from .. import googleauth, secretlib

    if not secretlib.available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "SECRETS_KEY is not set on this server, so the "
                            "stored credential cannot be read")

    row = await db.pool().fetchrow(
        "SELECT key_enc FROM provider_keys WHERE provider = 'google' "
        "ORDER BY campaign_id NULLS FIRST, id LIMIT 1")
    if row is None:
        return RateImport(written=[], missing=[],
                          note="No Google service account is stored yet - add "
                               "one on a client or campaign first.")

    try:
        creds = googleauth.parse(secretlib.crypto().decrypt(row["key_enc"]))
        token, _ = await asyncio.to_thread(googleauth.mint, creds)
    except googleauth.AuthError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    try:
        prices = await asyncio.to_thread(_fetch_google_tts_prices, token)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Google returned {e.code} for its price catalogue. "
                            f"{detail}")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"could not read Google's prices: {type(e).__name__}")

    if not prices:
        return RateImport(written=[], missing=[],
                          note="Google's catalogue listed no per-character "
                               "text-to-speech prices.")

    today = datetime.date.today().isoformat()
    written = []
    for family in sorted(prices):
        await db.pool().execute(
            """INSERT INTO provider_rates
                   (provider, model, kind, unit, price, currency, note, updated_by)
               VALUES ('google', $1, 'tts_characters', 'per_million', $2, 'USD',
                       $3, $4)
               ON CONFLICT (provider, coalesce(model, ''), kind) DO UPDATE
                   SET unit = EXCLUDED.unit, price = EXCLUDED.price,
                       currency = EXCLUDED.currency, note = EXCLUDED.note,
                       updated_at = now(), updated_by = EXCLUDED.updated_by""",
            family, prices[family], f"From Google's billing catalogue, {today}",
            actor.id)
        written.append(family)

    await audit.record(actor, entity="provider_rate", entity_id="google",
                       action="import",
                       changes={"families": {"from": None,
                                             "to": ", ".join(written)}})
    return RateImport(
        written=written, missing=[],
        note=f"Prices read from Google's billing catalogue on {today}, per "
             f"voice family. A campaign prices by the family in its "
             f"text-to-speech model; calls made before that field held a "
             f"family will show no rate.")


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
