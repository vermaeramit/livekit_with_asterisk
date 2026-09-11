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

# openrouter speaks OpenAI's wire format, so it needs no new plugin - only
# a key and a base_url. What it buys is every model it fronts.
PROVIDERS = ("openai", "sarvam", "soniox", "openrouter")
Provider = Literal["openai", "sarvam", "soniox", "openrouter"]

_TIMEOUT = 15


@dataclass(frozen=True)
class Validation:
    ok: bool
    message: str
    # The key authenticates but the account cannot pay. Not a reason to refuse
    # the save - the key is correct and the balance is a separate problem the
    # client can fix without touching us - but the console must say so, loudly.
    no_credits: bool = False
    # The key works and something it will be asked to do does not. Same
    # principle as no_credits and a different sentence each time, so it carries
    # the text rather than a flag. A save with one of these still succeeds; the
    # console shows it instead of the ordinary "saved".
    warning: str | None = None


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


def _embed_model() -> str:
    """The model the knowledge base actually embeds with.

    Read from kb.py rather than written out again here. A check that validates
    a different model from the one that runs is a check that passes while the
    thing it guards is broken - which is the exact failure this function exists
    to catch, so it would be a poor place to reintroduce it.
    """
    try:
        from . import kblib
        if kblib.available():
            return kblib.kb().EMBED_MODEL
    except Exception:
        pass
    return "text-embedding-3-small"


def _openai_can_embed(key: str) -> Validation | None:
    """-> a warning if this key cannot embed, or None if it can.

    /v1/models answers 200 for a project-scoped key whose allowed-models list
    does not include the embedding model. That is not a hypothetical: it is
    what happened here. Chat kept working, retrieval was dead, and nobody found
    out for weeks because a separate bug meant retrieval was never attempted.

    So this asks the question that matters - "can this key do the work" rather
    than "is this key genuine" - by doing one word's worth of it. The cost is a
    single token, which is the same reason _check_sarvam synthesises one
    character instead of trusting an endpoint that answers 200 to anything.
    """
    model = _embed_model()
    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=json.dumps({"model": model, "input": "ok"}).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST")
    code, body = _status_of(req)
    if code == 200:
        return None
    if code == 429:
        # Rate limited, not refused. The key can embed; it is busy.
        return None
    if code in (403, 404) or "model_not_found" in body:
        return Validation(
            True,
            f"key saved, but this OpenAI project cannot use {model}",
            warning=(f"The key works for the language model, but this project "
                     f"is not allowed to use {model}. The knowledge base needs "
                     f"it: without it every search fails and the agent answers "
                     f"from its own training instead of your documents. Add it "
                     f"under Project → Limits → Allowed models."))
    # Anything else is not a clear answer, and inventing a warning from an
    # unclear one would train somebody to ignore warnings.
    log.warning("openai embedding check inconclusive: %s", code)
    return None


def _get_json(req: urllib.request.Request) -> tuple[int, dict | None, str]:
    """Like _status_of, but keeps the body on SUCCESS as well.

    Separate rather than folded in, because _status_of is used by checks that
    hit endpoints whose 200 body is large and useless - /v1/models chief among
    them - and reading those into memory on every key save buys nothing.

    Bounded anyway: a status body that is not small is not a status body.
    """
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            raw = r.read(4096).decode("utf-8", "replace")
        try:
            return r.status, json.loads(raw), ""
        except Exception:
            return r.status, None, ""
    except urllib.error.HTTPError as e:
        try:
            return e.code, None, e.read(512).decode("utf-8", "replace")
        except Exception:
            return e.code, None, ""
    except Exception as e:
        return 0, None, f"{type(e).__name__}: {e}"


def _check_openai(key: str) -> Validation:
    # /v1/models is free and authenticated - a wrong key returns 401. Verified
    # against the live API rather than assumed.
    req = urllib.request.Request(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    code, body = _status_of(req)
    if code == 200:
        # Authenticated. Now the question that /v1/models cannot answer.
        return _openai_can_embed(key) or Validation(True, "key accepted by OpenAI")
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


def _check_openrouter(key: str) -> Validation:
    """GET /v1/key - authenticated, free, and it answers both questions.

    Both, because a key that authenticates and has no credit left fails on the
    first real call and looks exactly like a broken key. This endpoint returns
    limit_remaining alongside the auth result, so the console can say which it
    is - the same distinction _check_sarvam buys with a 402.

    What it cannot check is the MODEL. On a gateway the model name is the
    routing and it is chosen per campaign, long after the key is saved, so that
    one is found at call time. Said here rather than implied.
    """
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {key}"})
    code, payload, err = _get_json(req)
    if code in (401, 403):
        return Validation(False, "OpenRouter rejected this key")
    if code == 0:
        return Validation(False, f"could not reach OpenRouter: {err}")
    if code != 200:
        return Validation(False, f"OpenRouter returned {code}")

    # A limit of null means unlimited, which is not the same as zero.
    data = (payload or {}).get("data") or {}
    left = data.get("limit_remaining")
    if left is not None and float(left) <= 0:
        return Validation(True, "key is valid but the OpenRouter account has "
                                "no credit left", no_credits=True)
    return Validation(True, "key accepted by OpenRouter")


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


def _check_soniox(key: str) -> Validation:
    """GET /v1/models - free, read-only, and genuinely authenticated.

    Every Soniox endpoint tried (models, transcriptions, files, voices) answered
    401 to a made-up key, so unlike Sarvam's /v1/models there is no trap here.
    The 401 path is verified against the live API; the 200 path is not, because
    there was no funded account when this was written. If a valid key ever
    reports as rejected, that is the first thing to re-check.
    """
    req = urllib.request.Request(
        "https://api.soniox.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    code, body = _status_of(req)
    if code == 200:
        return Validation(True, "key accepted by Soniox")
    if code in (401, 403):
        return Validation(False, "Soniox rejected this key")
    if code in (402, 429):
        return Validation(True, "key is valid but the Soniox account is out of "
                                "credit or rate limited", no_credits=True)
    if code == 0:
        return Validation(False, f"could not reach Soniox: {body}")
    return Validation(False, f"Soniox returned {code}")


_CHECKS = {"openai": _check_openai, "sarvam": _check_sarvam,
           "soniox": _check_soniox, "openrouter": _check_openrouter}


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


async def resolve(*, tenant_id: int, campaign_id: int | None) -> dict[str, str]:
    """-> {'openai': '...', 'sarvam': '...'}, decrypted.

    The ONLY function here that produces plaintext. Everything else works from
    hints. Used by knowledge-base ingestion, which embeds on the client's key so
    the cost lands with the documents rather than with us.

    Mirrors the agent's store.load_provider_keys(): campaign override first,
    client default second. The two resolve identically on purpose - a call and
    an ingest for the same campaign must not end up on different accounts.
    """
    c = secretlib.crypto()
    rows = await db.pool().fetch(
        """SELECT provider, key_enc
             FROM provider_keys
            WHERE tenant_id = $1
              AND (campaign_id = $2 OR campaign_id IS NULL)
            ORDER BY provider, campaign_id NULLS LAST""",
        tenant_id, campaign_id,
    )
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(r["provider"], c.decrypt(r["key_enc"]))
    return out


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


async def required_for_campaign(campaign_id: int) -> set[str]:
    """Which providers this campaign actually needs a key for.

    Its configured STT and TTS, plus openai - the LLM runs on it, and so does
    knowledge-base retrieval, whatever STT and TTS are set to.

    NOT every provider in PROVIDERS. That was the rule until soniox was added,
    at which point it would have demanded a Soniox key from clients who never
    use Soniox before letting them enable anything.

    Fallbacks are deliberately excluded: a fallback with no key is skipped with
    a warning at call time, not a reason to block the campaign.
    """
    row = await db.pool().fetchrow(
        """SELECT stt_provider, tts_provider FROM agent_config
            WHERE campaign_id = $1 ORDER BY id LIMIT 1""",
        campaign_id,
    )
    if row is None:
        return {"openai"}
    return {row["stt_provider"], row["tts_provider"], "openai"}


async def missing_for_campaign(campaign_id: int) -> list[str]:
    """Providers a campaign needs and has no usable key for.

    Used to block enabling a campaign. Catching this at config time is the whole
    point: the alternative is catching it at call time, where the symptom is a
    caller being handed to a human and nobody being told why.
    """
    needed = await required_for_campaign(campaign_id)
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
    return sorted(needed - have)
