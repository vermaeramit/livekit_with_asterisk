"""Provider key endpoints.

Write-only by design. A key goes in through PUT and never comes back out: no
endpoint here returns one, and the audit trail records that a key changed, not
what it changed to. The console works from `hint` (last four characters) alone,
which is enough to answer "is this the key I just pasted?" and useless to anyone
who gets hold of the response.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from .. import ttspreview
from .. import audit, db, provider_keys as pk, secretlib
from ..deps import (CurrentUser, active_user, assert_campaign_visible,
                    require_perm, tenant_scope)
from ..schemas import (ProviderKeyOut, ProviderKeySet, ProviderKeyWritten,
                       TtsPreviewIn,
                       TtsCatalog, TtsModel, TtsVoice)

router = APIRouter(tags=["provider keys"])

editor = require_perm("provider_keys.write")


def _check_provider(provider: str) -> str:
    if provider not in pk.PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"unknown provider '{provider}'")
    return provider


def _require_crypto() -> None:
    if not secretlib.available():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"provider key encryption unavailable: {secretlib.why_unavailable()}")


async def _assert_tenant_visible(user: CurrentUser, tenant_id: int) -> int:
    # tenant_scope refuses a mismatch; the existence check keeps a superadmin
    # from writing keys for a tenant that was deleted mid-session.
    tenant_scope(user, tenant_id)
    exists = await db.pool().fetchval(
        "SELECT 1 FROM tenants WHERE id = $1", tenant_id)
    if not exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    return tenant_id


# --------------------------------------------------------------------------
# client-level: the default for every campaign the client owns
# --------------------------------------------------------------------------

@router.get("/clients/{tenant_id}/keys", response_model=list[ProviderKeyOut])
async def list_client_keys(tenant_id: int,
                           user: CurrentUser = Depends(active_user)):
    await _assert_tenant_visible(user, tenant_id)
    return [ProviderKeyOut(**r)
            for r in await pk.status_for(tenant_id=tenant_id, campaign_id=None)]


@router.put("/clients/{tenant_id}/keys/{provider}",
            response_model=ProviderKeyWritten)
async def set_client_key(tenant_id: int, provider: str, body: ProviderKeySet,
                         actor: CurrentUser = Depends(editor)):
    _require_crypto()
    _check_provider(provider)
    await _assert_tenant_visible(actor, tenant_id)

    result = await pk.validate(provider, body.key)
    if not result.ok:
        # 422, not 400: the value is well-formed, the provider disagrees with it.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.message)

    hint = await pk.store(tenant_id=tenant_id, campaign_id=None,
                          provider=provider, key=body.key, actor_id=actor.id)
    # The hint, never the key. This table is readable by every tenant admin in
    # the tenant.
    await audit.record(actor, entity="provider_key", entity_id=provider,
                       action="set", tenant_id=tenant_id,
                       changes={"scope": "client", "hint": hint})
    return ProviderKeyWritten(provider=provider, hint=hint,
                              message=result.message,
                              no_credits=result.no_credits)


@router.delete("/clients/{tenant_id}/keys/{provider}",
               status_code=status.HTTP_204_NO_CONTENT)
async def delete_client_key(tenant_id: int, provider: str,
                            actor: CurrentUser = Depends(editor)):
    _check_provider(provider)
    await _assert_tenant_visible(actor, tenant_id)
    if not await pk.remove(tenant_id=tenant_id, campaign_id=None,
                           provider=provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no key set")
    await audit.record(actor, entity="provider_key", entity_id=provider,
                       action="delete", tenant_id=tenant_id,
                       changes={"scope": "client"})


# --------------------------------------------------------------------------
# campaign-level: an override for one campaign
# --------------------------------------------------------------------------

@router.get("/campaigns/{campaign_id}/keys", response_model=list[ProviderKeyOut])
async def list_campaign_keys(campaign_id: int,
                             user: CurrentUser = Depends(active_user)):
    tenant_id = await assert_campaign_visible(user, campaign_id)
    return [ProviderKeyOut(**r)
            for r in await pk.status_for(tenant_id=tenant_id,
                                         campaign_id=campaign_id)]


@router.put("/campaigns/{campaign_id}/keys/{provider}",
            response_model=ProviderKeyWritten)
async def set_campaign_key(campaign_id: int, provider: str, body: ProviderKeySet,
                           actor: CurrentUser = Depends(editor)):
    _require_crypto()
    _check_provider(provider)
    tenant_id = await assert_campaign_visible(actor, campaign_id)

    result = await pk.validate(provider, body.key)
    if not result.ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.message)

    hint = await pk.store(tenant_id=tenant_id, campaign_id=campaign_id,
                          provider=provider, key=body.key, actor_id=actor.id)
    await audit.record(actor, entity="provider_key", entity_id=provider,
                       action="set", tenant_id=tenant_id, campaign_id=campaign_id,
                       changes={"scope": "campaign", "hint": hint})
    return ProviderKeyWritten(provider=provider, hint=hint,
                              message=result.message,
                              no_credits=result.no_credits)


@router.delete("/campaigns/{campaign_id}/keys/{provider}",
               status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign_key(campaign_id: int, provider: str,
                              actor: CurrentUser = Depends(editor)):
    """Remove the override. The campaign falls back to the client's key."""
    _check_provider(provider)
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    if not await pk.remove(tenant_id=tenant_id, campaign_id=campaign_id,
                           provider=provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "this campaign has no override for that provider")
    await audit.record(actor, entity="provider_key", entity_id=provider,
                       action="delete", tenant_id=tenant_id,
                       campaign_id=campaign_id, changes={"scope": "campaign"})


# --------------------------------------------------------------------------
# what the provider offers, asked of the provider
# --------------------------------------------------------------------------
# The only read here that USES a key. It still never returns one: the key goes
# into an Authorization header, the response is voices and models.
#
# It exists because the console's hardcoded Soniox voice list was wrong and had
# no way of knowing. It held the union of two models, so it offered Meera -
# which is on tts-rt-v1 and not on tts-rt-v2 - and a voice the model does not
# have raises inside TTS.__init__, killing the job before the call is answered.
# Nothing about that is visible on the campaign form.

# Sourced from Soniox's own documentation on 14 Aug 2026, because the API does
# not report it. Advisory text only - nothing branches on this.
_RETIRING = {"tts-rt-v1": "Soniox removes this on 31 Aug 2026",
             "tts-rt-v1-preview": "an alias of tts-rt-v1, removed 31 Aug 2026"}

_CATALOG_URLS = {"soniox": "https://api.soniox.com/v1/tts-models"}

# Opening the campaign form should not hit Soniox every time, and the list
# changes about as often as they ship a model.
_cache: dict[str, tuple[float, TtsCatalog]] = {}
# Preview audio, keyed on everything that changes it. Seventy voices means
# seventy clicks while somebody makes up their mind, and each one is a real
# synthesis billed to the campaign - the second click on the same voice
# should not be a second charge.
_preview_cache: dict[str, bytes] = {}
_CACHE_TTL = 600


def _fetch_soniox_models(key: str) -> dict:
    req = urllib.request.Request(
        _CATALOG_URLS["soniox"],
        headers={"Authorization": f"Bearer {key}",
                 # urllib's default is a WAF magnet - see agent/tools.py.
                 "User-Agent": os.getenv("TOOL_USER_AGENT", "AIVoice-Agent/1.0")})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


@router.post("/campaigns/{campaign_id}/tts-preview")
async def tts_preview(campaign_id: int, body: TtsPreviewIn,
                      user: CurrentUser = Depends(active_user)):
    """Hear a voice before choosing it, on the campaign's own key.

    Campaign-scoped because the KEY is: previews are billed to whoever owns the
    campaign, exactly as its calls are, and a platform-wide page would spend
    one client's money to answer another's question.

    Cached in memory by everything that changes the audio. Seventy voices means
    seventy clicks while somebody makes up their mind, and the second click on
    the same voice should not be a second charge.
    """
    _check_provider(body.provider)
    tenant_id = await assert_campaign_visible(user, campaign_id)
    _require_crypto()

    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nothing to say")

    cache_key = (f"{body.provider}:{body.model}:{body.voice}:{body.language}:"
                 f"{body.speed}:{tenant_id}:{hash(text)}")
    hit = _preview_cache.get(cache_key)
    if hit:
        return Response(content=hit, media_type="audio/mpeg",
                        headers={"Cache-Control": "no-store"})

    keys = await pk.resolve(tenant_id=tenant_id, campaign_id=campaign_id)
    if not keys.get(body.provider):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"no {body.provider} key on this campaign or client - a preview is "
            "synthesised for real, on your own key")

    synth = {"soniox": ttspreview.soniox,
             "sarvam": ttspreview.sarvam}.get(body.provider)
    if synth is None:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            f"previews are not wired up for {body.provider} yet")

    try:
        audio = await synth(
            keys[body.provider], model=body.model, voice=body.voice,
            language=body.language, text=text, speed=body.speed)
    except ttspreview.PreviewError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    # Bounded, and crudely: this is a convenience cache in one process, not a
    # store. Dropping the oldest half keeps it from growing without a
    # dependency or an eviction policy nobody will tune.
    if len(_preview_cache) > 200:
        for k in list(_preview_cache)[:100]:
            _preview_cache.pop(k, None)
    _preview_cache[cache_key] = audio

    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})


@router.get("/campaigns/{campaign_id}/tts-catalog/{provider}",
            response_model=TtsCatalog)
async def tts_catalog(campaign_id: int, provider: str,
                      user: CurrentUser = Depends(active_user)):
    """Models and voices the campaign's own key can actually use."""
    _check_provider(provider)
    if provider not in _CATALOG_URLS:
        # Sarvam publishes no such endpoint; its speakers are documented only.
        # Empty rather than 404, so the console can ask unconditionally and fall
        # back to its static list without special-casing per provider.
        return TtsCatalog(provider=provider, models=[])

    tenant_id = await assert_campaign_visible(user, campaign_id)
    _require_crypto()

    cache_key = f"{provider}:{tenant_id}:{campaign_id}"
    hit = _cache.get(cache_key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]

    keys = await pk.resolve(tenant_id=tenant_id, campaign_id=campaign_id)
    if not keys.get(provider):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"no {provider} key on this campaign or client - add one first, "
            "the voice list comes from the provider")

    try:
        raw = await asyncio.to_thread(_fetch_soniox_models, keys[provider])
    except Exception as e:
        # The provider, not the key. Never let a failure here carry the secret.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"could not read the {provider} catalogue: "
                            f"{type(e).__name__}")

    models = [
        TtsModel(id=m["id"], name=m.get("name"),
                 retiring=_RETIRING.get(m["id"]),
                 voices=[TtsVoice(id=v["id"], gender=v.get("gender"),
                                  description=v.get("description"))
                         for v in m.get("voices") or []])
        for m in raw.get("models") or []
    ]
    # Retiring models last: the newest should be the obvious pick, and the one
    # with a removal date should take deliberate effort to select.
    models.sort(key=lambda m: (m.retiring is not None, m.id), reverse=False)

    out = TtsCatalog(provider=provider, models=models)
    _cache[cache_key] = (time.monotonic(), out)
    return out
