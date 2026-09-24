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
from ..schemas import (LlmCatalog, LlmModel,
                       ProviderKeyOut, ProviderKeySet, ProviderKeyWritten,
                       TtsPreviewIn,
                       TtsCatalog, TtsModel, TtsVoice)

router = APIRouter(tags=["provider keys"])

editor = require_perm("provider_keys.write")


def _check_provider(provider: str) -> str:
    """For the endpoints that SET a key. Deliberately strict.

    kokoro is not in PROVIDERS and must not be: there is no account behind our
    own GPU box, so "set a key for kokoro" is a request to store a fiction.
    """
    if provider not in pk.PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"unknown provider '{provider}'")
    return provider


def _check_voice_provider(provider: str) -> str:
    """For previewing a voice and listing voices, where a keyless provider is
    a perfectly ordinary choice - it just speaks on hardware we own."""
    if provider not in pk.PROVIDERS and provider not in pk.KEYLESS:
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

    result = await pk.validate(provider, body.key, body.region)
    if not result.ok:
        # 422, not 400: the value is well-formed, the provider disagrees with it.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.message)

    hint = await pk.store(tenant_id=tenant_id, campaign_id=None,
                          provider=provider, key=body.key, actor_id=actor.id,
                          region=body.region)
    # The hint, never the key. This table is readable by every tenant admin in
    # the tenant.
    await audit.record(actor, entity="provider_key", entity_id=provider,
                       action="set", tenant_id=tenant_id,
                       changes={"scope": "client", "hint": hint,
                                "region": body.region})
    return ProviderKeyWritten(provider=provider, hint=hint,
                              message=result.message,
                              no_credits=result.no_credits,
                              warning=result.warning)


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

    result = await pk.validate(provider, body.key, body.region)
    if not result.ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.message)

    hint = await pk.store(tenant_id=tenant_id, campaign_id=campaign_id,
                          provider=provider, key=body.key, actor_id=actor.id,
                          region=body.region)
    await audit.record(actor, entity="provider_key", entity_id=provider,
                       action="set", tenant_id=tenant_id, campaign_id=campaign_id,
                       changes={"scope": "campaign", "hint": hint,
                                "region": body.region})
    return ProviderKeyWritten(provider=provider, hint=hint,
                              message=result.message,
                              no_credits=result.no_credits,
                              warning=result.warning)


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

# Soniox is the only provider with a machine-readable voice list. Its host
# depends on the region the key belongs to, so the URL cannot be a constant -
# a US host asked with an India key answers 401, and this endpoint would report
# that as "could not read the soniox catalogue" with no hint of the real cause.
_HAS_CATALOG = ("soniox", "kokoro")


def _fetch_kokoro_voices(url: str) -> list[str]:
    """-> the voice names our own server is serving right now.

    Read from the box rather than listed here for the reason the Soniox
    catalogue is read from Soniox: a literal goes stale and nobody notices
    until a call fails. This one can change without a release - a voice pack
    added to /opt/models on the GPU box appears here on the next page load.
    """
    req = urllib.request.Request(f"{url.rstrip('/')}/audio/voices")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    # The server has answered with both shapes across versions: a bare list,
    # and {"voices": [...]}. Neither is worth a 500.
    voices = data.get("voices") if isinstance(data, dict) else data
    return [v for v in (voices or []) if isinstance(v, str)]


# What the prefixes mean, from Kokoro's own naming: first letter the language,
# second the speaker's gender. Only the ones this system has a use for are
# named; anything else is shown as it comes.
_KOKORO_LANG = {"hf": "Hindi, female", "hm": "Hindi, male",
                "af": "English (US), female", "am": "English (US), male",
                "bf": "English (UK), female", "bm": "English (UK), male"}

# Opening the campaign form should not hit Soniox every time, and the list
# changes about as often as they ship a model.
_cache: dict[str, tuple[float, TtsCatalog]] = {}
# Preview audio, keyed on everything that changes it. Seventy voices means
# seventy clicks while somebody makes up their mind, and each one is a real
# synthesis billed to the campaign - the second click on the same voice
# should not be a second charge.
_preview_cache: dict[str, bytes] = {}
_CACHE_TTL = 600


def _fetch_soniox_models(key: str, region: str | None = None) -> dict:
    req = urllib.request.Request(
        f"https://{pk.soniox_host('api', region)}/v1/tts-models",
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
    _check_voice_provider(body.provider)
    tenant_id = await assert_campaign_visible(user, campaign_id)
    _require_crypto()

    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nothing to say")

    regions = await pk.resolve_regions(tenant_id=tenant_id,
                                       campaign_id=campaign_id)
    region = regions.get(body.provider)
    cache_key = (f"{body.provider}:{region or 'us'}:{body.model}:{body.voice}:"
                 f"{body.language}:{body.speed}:{tenant_id}:{hash(text)}")
    hit = _preview_cache.get(cache_key)
    if hit:
        return Response(content=hit, media_type="audio/mpeg",
                        headers={"Cache-Control": "no-store"})

    keys = await pk.resolve(tenant_id=tenant_id, campaign_id=campaign_id)
    # Our own box has no key to be missing - see pk.KEYLESS.
    if body.provider not in pk.KEYLESS and not keys.get(body.provider):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"no {body.provider} key on this campaign or client - a preview is "
            "synthesised for real, on your own key")

    synth = {"soniox": ttspreview.soniox,
             "sarvam": ttspreview.sarvam,
             "openai": ttspreview.openai,
             "kokoro": ttspreview.kokoro}.get(body.provider)
    if synth is None:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            f"previews are not wired up for {body.provider} yet")

    # Only the providers that have regions are told one. See pk.validate for
    # why the others are not handed an argument they do not understand.
    regional = {"region": region} if body.provider in pk.REGIONAL else {}
    try:
        audio = await synth(
            keys.get(body.provider, ""), model=body.model, voice=body.voice,
            language=body.language, text=text, speed=body.speed, **regional)
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


_LLM_CATALOG_URLS = {
    "openai": "https://api.openai.com/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
}

_llm_cache: dict[str, tuple[float, LlmCatalog]] = {}

# OpenAI's /v1/models lists everything the key can reach - transcription,
# speech, images, embeddings, moderation - with nothing saying which of them
# holds a conversation. There is no capability field to read, so this is a
# filter on the SHAPE OF THE NAME and nothing more. Said plainly because a
# heuristic presented as a capability check is how a console ends up offering
# whisper-1 as a language model.
_NOT_CHAT = ("tts", "transcribe", "whisper", "audio", "realtime", "image",
             "embedding", "moderation", "dall-e", "search", "codex")


def _fetch_models(url: str, key: str) -> list[dict]:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {key}",
                 # urllib's default is a WAF magnet - see agent/tools.py, and
                 # _fetch_soniox_models above.
                 "User-Agent": os.getenv("TOOL_USER_AGENT", "AIVoice-Agent/1.0")})
    # 15, the same as the voice catalogue beside it. OpenRouter's list is a few
    # hundred models and around a megabyte, so this is not instant.
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace")).get("data") or []


def _openai_models(raw: list[dict]) -> list[LlmModel]:
    out = []
    for m in raw:
        mid = m.get("id") or ""
        if not mid.startswith(("gpt-", "o1", "o3", "o4")):
            continue
        if any(bad in mid for bad in _NOT_CHAT):
            continue
        out.append(LlmModel(id=mid, name=mid))
    return sorted(out, key=lambda m: m.id)


def _openrouter_models(raw: list[dict]) -> list[LlmModel]:
    """Only the models that can actually run this agent.

    Filtered on tool calling, because every campaign here has tools and a model
    without them fails silently: the agent asks for a dealer lookup, the model
    answers from its own head, and the caller is told something invented. A
    dropdown that offers models which cannot do the job is a trap.

    Sorted by input price, cheapest first - which is the reason this provider is
    here at all - and each entry carries its price and context window so the
    choice can be made in the list rather than in another tab.
    """
    out = []
    for m in raw:
        mid = m.get("id") or ""
        if "tools" not in (m.get("supported_parameters") or []):
            continue
        pricing = m.get("pricing") or {}
        try:
            # OpenRouter quotes per TOKEN as a string. Per million is what
            # every price page uses and what anybody comparing will expect.
            pin = float(pricing.get("prompt") or 0) * 1e6
            pout = float(pricing.get("completion") or 0) * 1e6
        except (TypeError, ValueError):
            pin = pout = 0.0
        ctx = m.get("context_length") or 0
        bits = [f"${pin:.2f}/M in", f"${pout:.2f}/M out"]
        if ctx:
            bits.append(f"{ctx // 1000}k ctx")
        out.append(LlmModel(id=mid, name=m.get("name") or mid,
                            detail=" · ".join(bits), input_price=pin))
    return sorted(out, key=lambda m: (m.input_price, m.id))


@router.get("/campaigns/{campaign_id}/llm-catalog/{provider}",
            response_model=LlmCatalog)
async def llm_catalog(campaign_id: int, provider: str,
                      user: CurrentUser = Depends(active_user)):
    """Language models the campaign's own key can actually use.

    Read from the provider rather than held as a list here, for the reason the
    voice catalogue is: a literal goes stale and nobody notices until a call
    fails. tts-rt-v1 was the hardcoded default on the day it was withdrawn.
    """
    _check_provider(provider)
    if provider not in _LLM_CATALOG_URLS:
        return LlmCatalog(provider=provider, models=[])

    tenant_id = await assert_campaign_visible(user, campaign_id)
    _require_crypto()

    cache_key = f"llm:{provider}:{tenant_id}:{campaign_id}"
    hit = _llm_cache.get(cache_key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]

    keys = await pk.resolve(tenant_id=tenant_id, campaign_id=campaign_id)
    if not keys.get(provider):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"no {provider} key on this campaign or client - add one first, "
            "the model list comes from the provider")

    try:
        raw = await asyncio.to_thread(_fetch_models,
                                      _LLM_CATALOG_URLS[provider], keys[provider])
    except Exception as e:
        # The provider, not the key. Never let a failure here carry the secret.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"could not read the {provider} catalogue: "
                            f"{type(e).__name__}")

    models = (_openrouter_models(raw) if provider == "openrouter"
              else _openai_models(raw))
    out = LlmCatalog(provider=provider, models=models)
    _llm_cache[cache_key] = (time.monotonic(), out)
    return out


@router.get("/campaigns/{campaign_id}/tts-catalog/{provider}",
            response_model=TtsCatalog)
async def tts_catalog(campaign_id: int, provider: str,
                      user: CurrentUser = Depends(active_user)):
    """Models and voices this campaign can actually use.

    For the vendors that is what their key can reach; for kokoro it is what our
    own box is serving, which needs no key at all.
    """
    _check_voice_provider(provider)
    if provider not in _HAS_CATALOG:
        # Sarvam publishes no such endpoint; its speakers are documented only.
        # Empty rather than 404, so the console can ask unconditionally and fall
        # back to its static list without special-casing per provider.
        return TtsCatalog(provider=provider, models=[])

    tenant_id = await assert_campaign_visible(user, campaign_id)

    if provider == "kokoro":
        # Ours. No key, no region, no tenant in the answer - the box serves the
        # same voices to every campaign, so this is cached by nothing but the
        # provider name.
        url = ttspreview.kokoro_url()
        if not url:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "KOKORO_URL is not set on this server, so the voice list "
                "cannot be read from our own text-to-speech")
        hit = _cache.get("kokoro")
        if hit and time.monotonic() - hit[0] < _CACHE_TTL:
            return hit[1]
        try:
            names = await asyncio.to_thread(_fetch_kokoro_voices, url)
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                f"could not reach our own text-to-speech: "
                                f"{type(e).__name__}")
        # Hindi first: this is a Hindi calling system, and 68 of the 72 voices
        # are not. They are still listed - a campaign may want English - but
        # they do not come before the four that will actually be used.
        names.sort(key=lambda v: (not v.startswith(("hf_", "hm_")), v))
        out = TtsCatalog(provider=provider, models=[
            TtsModel(id="kokoro", name="Kokoro (our own server)", voices=[
                TtsVoice(id=v, description=_KOKORO_LANG.get(v[:2]))
                for v in names])])
        _cache["kokoro"] = (time.monotonic(), out)
        return out

    _require_crypto()

    regions = await pk.resolve_regions(tenant_id=tenant_id,
                                       campaign_id=campaign_id)
    region = regions.get(provider)
    # The region is part of the cache key because it is part of the answer:
    # moving a key to another region and getting the old region's voice list
    # back is how a campaign ends up set to a voice its model does not have.
    cache_key = f"{provider}:{region or 'us'}:{tenant_id}:{campaign_id}"
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
        raw = await asyncio.to_thread(_fetch_soniox_models, keys[provider],
                                      region)
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
