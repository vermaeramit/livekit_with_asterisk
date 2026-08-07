"""Per-client / per-campaign provider credentials.

Keys are stored encrypted (see agent/crypto.py via secretlib) and are MANDATORY:
resolution is campaign -> client -> nothing. There is no platform fallback, so a
client whose key is missing or broken cannot quietly spend our credits.

Nothing in this module ever returns, logs or audits a key. The only thing that
leaves it is `hint` - the last four characters - which is all the console shows.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

from . import db, secretlib

log = logging.getLogger("admin-api")

PROVIDERS = ("openai", "sarvam")
Provider = Literal["openai", "sarvam"]

_TIMEOUT = 15


@dataclass(frozen=True)
class Validation:
    ok: bool
    message: str
    # The key authenticates but the account cannot pay. Not a reason to refuse
    # the save - the key is correct and the balance is a separate problem the
    # client can fix without touching us - but the console must say so, loudly.
    no_credits: bool = False


def _status_of(req: urllib.request.Request) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        # Read a bounded amount: a provider error body is small, but a truncated
        # read is better than pulling an unbounded response into a log line.
        try:
            body = e.read(512).decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _check_openai(key: str) -> Validation:
    # /v1/models is free and authenticated - a wrong key returns 401. Verified
    # against the live API rather than assumed.
    req = urllib.request.Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    code, body = _status_of(req)
    if code == 200:
        return Validation(True, "key accepted by OpenAI")
    if code in (401, 403):
        return Validation(False, "OpenAI rejected this key")
    if code == 429:
        # 429 here is a rate limit on the models endpoint, or a quota problem.
        # Either way the key itself authenticated.
        return Validation(True, "key accepted, but OpenAI is rate limiting",
                          no_credits=True)
    if code == 0:
        return Validation(False, f"could not reach OpenAI: {body}")
    return Validation(False, f"OpenAI returned {code}")


def _check_sarvam(key: str) -> Validation:
    """Sarvam has no free authenticated endpoint.

    /v1/models exists but answers 200 to a completely made-up key - it is not
    authenticated at all, so validating against it would mark every typo as
    valid and the campaign would only fail on its first real call.

    So this is a real one-character synthesis. It costs a negligible amount and
    buys something the OpenAI check cannot give: a 402 here means the key is
    genuine but the account is out of credits, which is exactly the failure that
    took a production campaign down mid-load-test.
    """
    payload = json.dumps({
        "text": "a",
        "target_language_code": "en-IN",
        "model": "bulbul:v3",
        "speaker": "shubh",
    }).encode()
    req = urllib.request.Request(
        "https://api.sarvam.ai/text-to-speech",
        data=payload,
        headers={"Content-Type": "application/json", "api-subscription-key": key},
        method="POST",
    )
    code, body = _status_of(req)
    if code == 200:
        return Validation(True, "key accepted by Sarvam")
    if code in (401, 403):
        return Validation(False, "Sarvam rejected this key")
    if code == 402:
        return Validation(True, "key is valid but the Sarvam account has no credits",
                          no_credits=True)
    if code == 0:
        return Validation(False, f"could not reach Sarvam: {body}")
    return Validation(False, f"Sarvam returned {code}")


_CHECKS = {"openai": _check_openai, "sarvam": _check_sarvam}


async def validate(provider: str, key: str) -> Validation:
    """Ask the provider whether this key works, before it is ever stored.

    A key is saved once and read on every call afterwards, so a typo that is not
    caught here is caught by a caller. This project has shipped three values from
    memory that were wrong and only surfaced on a live call; a save-time check is
    the cheapest place to stop the fourth.
    """
    check = _CHECKS[provider]
    return await asyncio.to_thread(check, key)


async def store(*, tenant_id: int, campaign_id: int | None, provider: str,
                key: str, actor_id: int) -> str:
    """Encrypt and upsert. -> the hint.

    The plaintext exists only as a local and is never handed back.
    """
    c = secretlib.crypto()
    enc, hint = c.encrypt(key), c.hint(key)
    await db.pool().execute(
        """INSERT INTO provider_keys
               (tenant_id, campaign_id, provider, key_enc, key_hint, updated_by)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (tenant_id, COALESCE(campaign_id, 0), provider)
           DO UPDATE SET key_enc    = EXCLUDED.key_enc,
                         key_hint   = EXCLUDED.key_hint,
                         updated_by = EXCLUDED.updated_by,
                         updated_at = now()""",
        tenant_id, campaign_id, provider, enc, hint, actor_id,
    )
    return hint


async def remove(*, tenant_id: int, campaign_id: int | None,
                 provider: str) -> bool:
    tag = await db.pool().execute(
        """DELETE FROM provider_keys
            WHERE tenant_id = $1
              AND campaign_id IS NOT DISTINCT FROM $2
              AND provider = $3""",
        tenant_id, campaign_id, provider,
    )
    return tag.endswith(" 1")


async def status_for(*, tenant_id: int,
                     campaign_id: int | None) -> list[dict]:
    """What the console shows: one row per provider, never a key.

    For a campaign this reports the EFFECTIVE key - its own override if it has
    one, otherwise the client's - because "which key will the next call use" is
    the only question worth answering here. `source` says which.
    """
    rows = await db.pool().fetch(
        """SELECT provider, campaign_id, key_hint, updated_at
             FROM provider_keys
            WHERE tenant_id = $1
              AND (campaign_id IS NULL OR campaign_id = $2)""",
        tenant_id, campaign_id,
    )
    own = {r["provider"]: r for r in rows if r["campaign_id"] is not None}
    inherited = {r["provider"]: r for r in rows if r["campaign_id"] is None}

    out = []
    for p in PROVIDERS:
        if campaign_id is not None and p in own:
            r, source = own[p], "campaign"
        elif p in inherited:
            r, source = inherited[p], "client"
        else:
            out.append({"provider": p, "source": "none",
                        "hint": None, "updated_at": None})
            continue
        out.append({"provider": p, "source": source,
                    "hint": r["key_hint"], "updated_at": r["updated_at"]})
    return out


async def missing_for_campaign(campaign_id: int) -> list[str]:
    """Providers a campaign has no usable key for.

    Used to block enabling a campaign. Catching this at config time is the whole
    point: the alternative is catching it at call time, where the symptom is a
    caller being handed to a human and nobody being told why.
    """
    rows = await db.pool().fetch(
        """SELECT DISTINCT pk.provider
             FROM campaigns c
             JOIN provider_keys pk
               ON pk.tenant_id = c.tenant_id
              AND (pk.campaign_id = c.id OR pk.campaign_id IS NULL)
            WHERE c.id = $1""",
        campaign_id,
    )
    have = {r["provider"] for r in rows}
    return [p for p in PROVIDERS if p not in have]
