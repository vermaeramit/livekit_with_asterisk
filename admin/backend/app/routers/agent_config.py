"""Per-campaign agent configuration.

The workers call store.load_config() inside the job entrypoint, so a save here
takes effect on the next call with no restart and no effect on calls already in
progress.
"""
from __future__ import annotations

import json
import logging
import re

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, db, holdaudio, kblib, secretlib
from .. import provider_keys as pk
from ..deps import CurrentUser, active_user, assert_campaign_visible, require_perm
from ..schemas import (AgentConfigOut, AgentConfigUpdate, AuditEntry,
                       CampaignRoute, CampaignRouteCreate, CopyHours,
                       DiallerIdCreate, DiallerIdOut,
                       PostbackOut, PromptTokens, PromptVersion,
                       FinalPrompt, PromptSection, PromptTool)

log = logging.getLogger("admin-api")

router = APIRouter(prefix="/campaigns/{campaign_id}", tags=["agent config"])

editor = require_perm("campaign.write")

FIELDS = (
    "language", "greeting", "instructions",
    "stt_provider", "stt_model", "stt_fallback_provider",
    "llm_provider", "llm_model", "llm_temperature",
    "llm_fallback_provider", "llm_fallback_model",
    "tts_provider", "tts_model", "tts_voice", "tts_fallback_provider",
    "allow_interrupt",
    "kb_enabled", "kb_top_k", "kb_min_score", "kb_inline_max_tokens", "kb_summary",
    "kb_filler_enabled", "kb_filler_message",
    "stt_context_terms",
    "max_turns", "max_duration_sec", "max_prompt_tokens", "limit_message",
    # Read by Asterisk over ODBC as well as by this page - see migration 046.
    "max_parallel_calls", "queue_message", "queue_audio_file",
    "queue_gap_seconds", "queue_max_wait_seconds", "queue_timeout_action",
    "queue_timeout_message", "queue_timeout_audio_file",
    "transfer_enabled", "transfer_to", "transfer_message",
    "transfer_confirm", "transfer_confirm_message",
    "transfer_dialler_id", "transfer_extension",
    "silence_timeout_sec", "silence_prompts", "end_call_marker",
    "transfer_marker",
    "transfer_hours_enabled", "transfer_hours", "transfer_holidays",
    "transfer_closed_message",
    "stt_endpoint_level", "stt_endpoint_sensitivity",
    "prompt_datetime", "prompt_timezone",
    "postback_enabled", "postback_url", "postback_auth_header",
    "postback_auth_value_hint", "postback_fields",
    "postback_include_transcript", "postback_full_payload",
    "postback_max_attempts",
    "postback_retry_after_sec",
    "recording_disclosure",
)

# A bracketed token used this often is a marker somebody meant, not an example.
# [Model], [Date] and [value] appear once each in a real prompt as placeholders
# in sample text; [CT] appeared eighteen times.
_MARKER_LIKE = re.compile(r"\[[A-Za-z_][A-Za-z0-9_]{1,14}\]")
_MARKER_MIN_USES = 3


def _tts_defaults():
    """The agent's own fallback names, read rather than copied.

    A console that names a voice the agent stopped using is worse than one that
    says nothing - so this reads the module the agent reads, and falls back to
    an object that says nothing if the mount is not there.
    """
    try:
        if kblib.available():
            return kblib.agent_module("tts_defaults")
    except Exception:
        log.exception("could not read the agent's TTS defaults")

    class _Unknown:
        SONIOX_VOICE = "its built-in voice"
        SONIOX_MODEL = "its built-in model"
    return _Unknown()


# The key out of {{cus_name}} or {{modalname|आपकी गाड़ी}}; the default after the
# pipe is not needed here. Deliberately a second copy of prompt.py's pattern
# rather than an import of it: this one only has to find the keys to check, and
# the agent's has to render them.
_PLACEHOLDER_KEY = re.compile(r"\{\{\s*([a-zA-Z_]+)")


def _speakable_dialler_fields() -> set[str] | None:
    """Which dialler fields may appear in something said to the caller.

    Read from the agent's own prompt.py, so there is one list rather than a copy
    here that drifts.

    None, not an empty set, when it cannot be read. An empty set would make the
    check below accuse every placeholder of being forbidden, and a console that
    confidently reports a fault it cannot actually see is worse than one that
    says nothing.
    """
    try:
        if kblib.available():
            return set(kblib.agent_module("prompt").PROMPT_SAFE)
    except Exception:
        log.exception("could not read the agent's speakable dialler fields")
    return None


def _warnings(cfg: dict) -> list[str]:
    """Things that are wrong but not invalid, so a save is never blocked.

    The one that prompted this: a campaign's transfer_marker was [Transfer] and
    its prompt said [CT], eighteen times. The filter looked for a marker the
    model was never asked to write, so no call ever transferred - and nothing
    said so until a caller asked for a person and did not get one. The two
    fields live on different tabs and nothing had ever compared them.
    """
    out: list[str] = []
    instructions = cfg.get("instructions") or ""
    counts: dict[str, int] = {}
    for m in _MARKER_LIKE.findall(instructions):
        counts[m] = counts.get(m, 0) + 1

    for field, label in (("transfer_marker", "Transfer marker"),
                         ("end_call_marker", "End-of-call marker")):
        marker = (cfg.get(field) or "").strip()
        if not marker or marker in instructions:
            continue
        # Configured, and the prompt never asks for it. Name the token the
        # prompt DOES lean on, because that is almost always the intended one.
        likely = sorted(((n, t) for t, n in counts.items()
                         if n >= _MARKER_MIN_USES and t != marker), reverse=True)
        suggestion = (f" The prompt uses {likely[0][1]} {likely[0][0]} times — "
                      f"did you mean that?") if likely else ""
        out.append(
            f"{label} is {marker}, but the prompt never writes it, so it will "
            f"never fire.{suggestion}")

    # A fallback with no model is not a fallback. The agent logs this and runs
    # on one leg, which is the right behaviour and the wrong moment to find out
    # - by then there is a caller on the line and the primary has already
    # failed. Unlike STT and TTS there is no provider default to fall back to:
    # on a gateway the model name is the routing.
    if (cfg.get("llm_fallback_provider")
            and not (cfg.get("llm_fallback_model") or "").strip()):
        out.append(
            f"The {cfg['llm_fallback_provider']} fallback has no model set, so "
            f"it will not be used — the campaign runs on one language model.")

    # A voice that is not stored anywhere is not "the provider's default" -
    # it is a name written in the agent's code, and Soniox has withdrawn seven
    # of them in one version change before. The console shows an empty box and
    # nothing else says which voice is really speaking.
    provider = cfg.get("tts_provider")
    if provider == "soniox":
        d = _tts_defaults()
        if not (cfg.get("tts_voice") or "").strip():
            out.append(
                f"No voice is chosen, so calls use {d.SONIOX_VOICE} — a "
                f"fallback in the agent, not a setting. Soniox has withdrawn "
                f"voices before, and one that is gone fails mid-call. Choose "
                f"one on the Voice tab.")
        if not (cfg.get("tts_model") or "").strip():
            out.append(
                f"No voice model is chosen, so calls use {d.SONIOX_MODEL} — a "
                f"fallback in the agent, not a setting.")
    elif provider == "openai" and (cfg.get("tts_voice") or "").strip():
        # Worth saying even though nothing is broken: the field is filled in,
        # looks obeyed, and is not sent. Somebody choosing a voice here and
        # hearing a different one has no way to find out why.
        out.append(
            "The voice is not used on OpenAI — the agent does not send one, so "
            "the plugin's own default speaks. The field applies to Soniox and "
            "Sarvam.")

    # A placeholder naming a dialler field that may not be spoken. It saves, it
    # renders as its default or as nothing, and the campaign never says the name
    # it was meant to - the same silent shape as a marker the prompt never
    # writes. Worth catching HERE, where somebody is looking at the field, rather
    # than in a worker journal after a caller has heard the gap.
    #
    # The stronger reason: before this, these rendered. {{lead_id}} in a greeting
    # read a CRM identifier out to the caller, walking around the curation that
    # exists precisely to stop that.
    speakable = _speakable_dialler_fields()
    if speakable is not None:
        for field, label in (("greeting", "The greeting"),
                             ("transfer_message", "The transfer message"),
                             ("transfer_closed_message", "The closed-hours message"),
                             ("limit_message", "The limit message"),
                             ("queue_message", "The hold message")):
            for key in dict.fromkeys(_PLACEHOLDER_KEY.findall(cfg.get(field) or "")):
                if key in speakable:
                    continue
                out.append(
                    f"{label} uses {{{{{key}}}}}, which is not a dialler field "
                    f"that may be spoken — it renders as its default, or as "
                    f"nothing. Available: "
                    f"{', '.join('{{' + k + '}}' for k in sorted(speakable))}.")

    # A concurrency limit with nothing to play. The caller who hits it hears
    # silence and is then handed off, which reads as a dropped call - and the
    # setting looks configured from the page, because the number is filled in.
    if cfg.get("max_parallel_calls") is not None:
        if not (cfg.get("queue_message") or "").strip():
            out.append(
                "Calls over the limit have nothing to listen to — no queue "
                "message is set, so they are handed off immediately.")
        elif not cfg.get("queue_audio_file"):
            out.append(
                "The queue message has not been synthesised yet, so it will "
                "not play. Save it again, and check for a provider error.")
        if cfg.get("max_parallel_calls") == 0:
            out.append(
                "The limit is 0, so this campaign takes no calls at all. "
                "Leave it empty for unlimited.")

    if (cfg.get("queue_timeout_action") == "hangup"
            and cfg.get("max_parallel_calls") is not None
            and not (cfg.get("queue_timeout_message") or "").strip()):
        out.append(
            "Callers who wait the full time are hung up on without being told "
            "anything, which sounds like a fault rather than a decision.")

    # Transfer hours that cannot do what they look like they do. Both of these
    # only show up when a real caller asks for a person out of hours, which is
    # the worst moment to discover a blank field.
    if cfg.get("transfer_hours_enabled"):
        hours = cfg.get("transfer_hours") or {}
        if not any(hours.get(d) for d in ("mon", "tue", "wed", "thu",
                                          "fri", "sat", "sun")):
            out.append(
                "Transfer hours are on but no day is open, which would refuse "
                "every handoff. Transfers are being allowed instead — set the "
                "days, or turn the hours off.")
        elif not (cfg.get("transfer_closed_message") or "").strip():
            out.append(
                "No out-of-hours message is set, so callers asking for a "
                "person after hours hear a generic sentence rather than "
                "yours.")
    return out


SELECT_CONFIG = f"""
    SELECT campaign_id, name, updated_at, {', '.join(FIELDS)}
      FROM agent_config WHERE campaign_id = $1 ORDER BY id LIMIT 1
"""


async def _get(campaign_id: int) -> dict:
    row = await db.pool().fetchrow(SELECT_CONFIG, campaign_id)
    if row is None:
        # Campaigns created through the panel always get one. A campaign without
        # a config predates the panel, and cannot take a call.
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "this campaign has no agent config")
    d = dict(row)
    # asyncpg returns JSONB as text without a codec. Named explicitly, because
    # forgetting one does not fail - the value arrives as a string and whatever
    # reads it quietly does the wrong thing.
    for col in ("postback_fields", "transfer_hours", "transfer_holidays",
                "stt_context_terms"):
        if isinstance(d.get(col), str):
            d[col] = json.loads(d[col])
    # Computed on the way out, so a mismatch already in the database shows the
    # moment somebody opens the page rather than only after the next save.
    d["warnings"] = _warnings(d)
    return d


async def _render_queue_audio(campaign_id: int, tenant_id: int | None,
                              cfg: dict) -> list[str]:
    """Make sure the hold audio on disk matches what the messages now say.

    Called after every save rather than only when the text changed, because the
    VOICE is part of what the file is. Change the campaign's voice and leave the
    message alone, and without this the callers on hold would keep hearing the
    old voice while everyone who got through hears the new one.

    Cheap when there is nothing to do: the name is a hash of the words and the
    voice, so an unchanged message is a file that already exists and a stat
    that finds it.

    -> the problems, to be shown with the save. Never raises: a provider that
    is down should not cost somebody the sentence they just typed.
    """
    pairs = (("queue_message", "queue_audio_file", "queue message"),
             ("queue_timeout_message", "queue_timeout_audio_file",
              "goodbye message"))
    if not any((cfg.get(t) or "").strip() for t, _, _ in pairs):
        # Nothing to say. Clear any paths left from a message that has since
        # been emptied - a stale path here is audio playing that the console no
        # longer shows anywhere.
        await db.pool().execute(
            "UPDATE agent_config SET queue_audio_file = NULL, "
            " queue_timeout_audio_file = NULL WHERE campaign_id = $1", campaign_id)
        return []

    provider = cfg["tts_provider"]
    keys = await pk.resolve(tenant_id=tenant_id, campaign_id=campaign_id)
    if not keys.get(provider):
        return [f"There is no {provider} key on this campaign, so the queue "
                f"message could not be synthesised and will not play."]

    problems: list[str] = []
    for text_col, file_col, label in pairs:
        text = (cfg.get(text_col) or "").strip()
        if not text:
            path = None
        else:
            try:
                path = await holdaudio.render(
                    provider=provider, api_key=keys[provider],
                    model=cfg.get("tts_model"), voice=cfg.get("tts_voice"),
                    language=cfg["language"], text=text)
            except holdaudio.RenderError as e:
                path = None
                problems.append(f"The {label} could not be synthesised: {e}")
            except Exception:
                path = None
                log.exception("hold render failed for campaign %s", campaign_id)
                problems.append(f"The {label} could not be synthesised.")

        if path != cfg.get(file_col):
            await db.pool().execute(
                f"UPDATE agent_config SET {file_col} = $2 WHERE campaign_id = $1",
                campaign_id, path)
    return problems


@router.get("/config/final-prompt", response_model=FinalPrompt)
async def final_prompt(campaign_id: int,
                       user: CurrentUser = Depends(active_user)):
    """The prompt as the model will actually receive it.

    Assembled from six places that nothing in the console showed together: the
    text on the Conversation tab, the knowledge base or its index, the grounding
    rules, the transfer rules, the opening hours, and the date. Reading them one
    tab at a time is how a rule ends up referring to a section that is not
    there - which is exactly what happened, and cost thirteen turns of a call
    answered from the model's own training instead of the customer's documents.

    It runs the agent's OWN build_instructions, not a copy. A preview
    reassembled here would be a preview of this endpoint, and the one thing it
    must never do is disagree with what goes down the wire.
    """
    if not kblib.available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"the agent library is not available: "
                            f"{kblib.why_unavailable()}")

    await assert_campaign_visible(user, campaign_id)
    row = await db.pool().fetchrow(
        "SELECT name FROM agent_config WHERE campaign_id = $1 ORDER BY id LIMIT 1",
        campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this campaign has no agent config")

    store = kblib.agent_module("store")
    pm = kblib.agent_module("prompt")
    ntok = kblib.kb().ntok

    # The same object the agent builds a call from, loaded the same way.
    cfg = await store.load_config(row["name"])
    cached, kb_mode, kb_tokens = await pm.build_instructions(cfg)

    # Appended per call and deliberately outside the cached prefix - see the
    # note beside it in the agent. Shown, because it is in what the model
    # receives, and marked, because it is the one part that differs call to
    # call and is paid for again every time.
    per_call = ""
    if getattr(cfg, "prompt_datetime", False):
        per_call = "\n\n" + pm.now_line(getattr(cfg, "prompt_timezone", None))

    sections = _split_prompt(pm, cfg, cached, kb_mode, per_call, ntok)

    return FinalPrompt(
        text=cached + per_call,
        cached_text=cached,
        kb_mode=kb_mode,
        kb_tokens=kb_tokens,
        total_tokens=ntok(cached + per_call),
        cached_tokens=ntok(cached),
        sections=sections,
        tools=await _tool_summary(campaign_id, ntok),
    )


def _split_prompt(pm, cfg, cached: str, kb_mode: str, per_call: str,
                  ntok) -> list[PromptSection]:
    """Break the finished prompt into the pieces it was made from.

    Built by walking the string rather than by re-running the assembly, so the
    parts always add up to the whole. Anything unaccounted for is returned as
    its own row instead of disappearing - a breakdown that quietly loses a
    section is worse than no breakdown, because it is believed.
    """
    out: list[PromptSection] = []
    rest = cached

    def take(name: str, source: str, piece: str) -> None:
        nonlocal rest
        if not piece or not rest.startswith(piece):
            return
        rest = rest[len(piece):]
        out.append(PromptSection(name=name, source=source, text=piece,
                                 tokens=ntok(piece)))

    take("Your instructions", "Conversation tab", cfg.instructions)

    # The knowledge block runs to its own end marker, so it can be lifted out
    # exactly however long it is.
    marker = "\n=== END ===\n"
    if rest.startswith("\n\n=== ") and marker in rest:
        end = rest.index(marker) + len(marker)
        piece, rest = rest[:end], rest[end:]
        out.append(PromptSection(
            name=("Knowledge base" if kb_mode == "full"
                  else "Knowledge base index"),
            source=("Knowledge tab — the documents themselves" if kb_mode == "full"
                    else "Knowledge tab — titles only, the agent searches for the rest"),
            text=piece, tokens=ntok(piece)))

    take("Knowledge rules", "Built in — written for this knowledge mode",
         pm.GROUNDING_FULL if kb_mode == "full" else pm.GROUNDING_INDEX)
    take("Handover rules", "Built in — added when handover is on", pm.TRANSFER_RULES)

    if rest.strip():
        out.append(PromptSection(
            name="Opening hours", source="Limits & handoff tab",
            text=rest, tokens=ntok(rest)))

    if per_call:
        out.append(PromptSection(
            name="Date and time", source="Added on every call, never cached",
            text=per_call, tokens=ntok(per_call)))
    return out


async def _tool_summary(campaign_id: int, ntok) -> list[PromptTool]:
    """The campaign's tools, as the model is shown them.

    Sent alongside the prompt rather than inside it, and paid for on every turn
    just the same - which is the reason to show them here at all.

    The two the agent adds itself - the knowledge search and the handover - are
    not in this list, because their definitions live in the agent behind a
    livekit decorator this process cannot import. Saying so is better than
    quietly presenting an incomplete total as a complete one.
    """
    store = kblib.agent_module("store")
    tools_mod = kblib.agent_module("tools")
    out: list[PromptTool] = []
    for spec in await store.load_tools(campaign_id):
        try:
            name, schema, _ = tools_mod.build_raw(spec, None, lambda *a, **k: None)
        except Exception:
            log.exception("could not describe tool for campaign %s", campaign_id)
            continue
        out.append(PromptTool(name=name,
                              json_schema=json.dumps(schema, indent=2),
                              tokens=ntok(json.dumps(schema))))
    return out


@router.get("/config", response_model=AgentConfigOut)
async def get_config(campaign_id: int, user: CurrentUser = Depends(active_user)):
    await assert_campaign_visible(user, campaign_id)
    return AgentConfigOut(**await _get(campaign_id))


@router.patch("/config", response_model=AgentConfigOut)
async def update_config(campaign_id: int, body: AgentConfigUpdate,
                        actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    before = await _get(campaign_id)

    fields = body.model_dump(exclude_unset=True)

    # Write-only, exactly like a provider key or a tool's auth value: it goes in
    # encrypted, and nothing ever hands it back. None = leave the stored secret
    # alone; "" = clear it. Conflating those means editing a URL silently wipes
    # the credential.
    secret = fields.pop("postback_auth_value", None)
    if secret is not None:
        if secret == "":
            fields["postback_auth_value_enc"] = None
            fields["postback_auth_value_hint"] = None
        else:
            c = secretlib.crypto()
            fields["postback_auth_value_enc"] = c.encrypt(secret)
            fields["postback_auth_value_hint"] = c.hint(secret)

    if not fields:
        return AgentConfigOut(**before)

    # The direction of this guard that gets forgotten.
    #
    # A campaign the dialler asks about MUST have a limit, because the answer is
    # that number - see migration 052. Refusing to ADD a dialler id without one
    # is the obvious half; this is the other, and it is the one that would
    # otherwise leave the dialler's endpoint reporting a 409 forever with
    # nothing on this page to say why.
    #
    # Enforced here rather than as a CHECK because the invariant spans two
    # tables - this column and campaign_dialler_ids - and a CHECK cannot see
    # across a row boundary.
    if "max_parallel_calls" in fields and fields["max_parallel_calls"] is None:
        ids = await db.pool().fetchval(
            "SELECT count(*) FROM campaign_dialler_ids WHERE campaign_id = $1",
            campaign_id)
        if ids:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"This campaign has {ids} dialler campaign id(s) registered, and "
                f"the dialler asks how many calls it can take. Remove the ids "
                f"first, or set a limit instead of clearing it.")

    unknown = set(fields) - set(FIELDS) - {"postback_auth_value_enc"}
    assert not unknown, f"schema and FIELDS disagree: {unknown}"

    # These are JSONB and asyncpg will not accept a Python list or dict for
    # them without the cast. Missing it is the same silent-wrong-type bug that
    # had bitten three times in the tools path when this comment was written,
    # and took transfer_hours the day it was added - the config page returned
    # 500 for every campaign until both this tuple and the decode above knew
    # about the new columns. Add a JSONB column, add it in BOTH places.
    JSON_COLS = ("postback_fields", "transfer_hours", "transfer_holidays",
                 "stt_context_terms")
    values = [json.dumps(v) if k in JSON_COLS and v is not None else v
              for k, v in fields.items()]
    sets = ", ".join(
        f"{k} = ${i}" + ("::jsonb" if k in JSON_COLS else "")
        for i, k in enumerate(fields, start=2))
    # The text as it was, before it is overwritten. Saved only when the prompt
    # actually changes, so editing a voice or a threshold does not fill the
    # list with identical copies.
    if "instructions" in fields and fields["instructions"] != before.get("instructions"):
        await _keep_version(campaign_id, tenant_id, before.get("instructions"),
                            actor.email)

    await db.pool().execute(
        f"UPDATE agent_config SET {sets}, updated_at = now() WHERE campaign_id = $1",
        campaign_id, *values)

    # After the write, so it renders what was actually saved.
    problems = await _render_queue_audio(campaign_id, tenant_id,
                                         await _get(campaign_id))

    await audit.record(actor, entity="agent_config", entity_id=before["name"],
                       action="update", tenant_id=tenant_id, campaign_id=campaign_id,
                       changes=audit.diff(before, fields))

    out = await _get(campaign_id)
    # The provider's own words, on the save that caused them. A later GET falls
    # back to the standing "has not been synthesised" warning, which is still
    # true and still points at the same field.
    out["warnings"] = out["warnings"] + problems
    return AgentConfigOut(**out)


@router.post("/config/copy-hours", response_model=AgentConfigOut)
async def copy_hours(campaign_id: int, body: CopyHours,
                     actor: CurrentUser = Depends(editor)):
    """Take another campaign's transfer hours and holidays.

    Hours are per campaign, which was the choice made when this was designed -
    but it means Diwali gets typed once per campaign, and a field that has to
    be typed five times is a field that ends up different in five places. This
    is the answer to that: set one campaign up properly and copy it.

    Deliberately copies hours, holidays and the closed message TOGETHER. The
    message usually names the hours ("we are open until 6:30"), so bringing one
    without the other produces a campaign that says something untrue.
    """
    await assert_campaign_visible(actor, campaign_id)
    # Checked separately: a user who can see this campaign cannot necessarily
    # see the one they are naming, and "copy from campaign 7" would otherwise
    # be a way to read another client's configuration one field at a time.
    await assert_campaign_visible(actor, body.from_campaign_id)
    if body.from_campaign_id == campaign_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "that is this campaign")

    source = await _get(body.from_campaign_id)
    before = await _get(campaign_id)

    await db.pool().execute(
        """UPDATE agent_config
              SET transfer_hours_enabled = $2, transfer_hours = $3,
                  transfer_holidays = $4, transfer_closed_message = $5,
                  updated_at = now()
            WHERE campaign_id = $1""",
        campaign_id,
        source["transfer_hours_enabled"],
        json.dumps(source["transfer_hours"]) if source["transfer_hours"] else None,
        json.dumps(source["transfer_holidays"] or []),
        source["transfer_closed_message"])

    await audit.record(actor, entity="agent_config", entity_id=str(campaign_id),
                       action="copy_hours",
                       changes={"transfer_hours":
                                {"from": before["transfer_hours"],
                                 "to": source["transfer_hours"]}})
    return AgentConfigOut(**await _get(campaign_id))


# Enough to go back through a week of edits, and bounded so a campaign that is
# tuned every hour does not grow without limit. The oldest go first; anything
# worth keeping longer is worth keeping outside a rolling list.
MAX_VERSIONS = 50


async def _keep_version(campaign_id: int, tenant_id: int | None,
                        text: str | None, who: str | None) -> None:
    """Store one previous prompt, and drop the oldest beyond the cap."""
    if not (text or "").strip():
        return
    try:
        n_tokens = None
        if kblib.available():
            n_tokens = kblib.kb().ntok(text)
    except Exception:
        # A token count is a nicety. Losing the version because counting it
        # failed would not be.
        n_tokens = None

    await db.pool().execute(
        """INSERT INTO prompt_versions (campaign_id, tenant_id, instructions,
                                        n_tokens, created_by)
           VALUES ($1, $2, $3, $4, $5)""",
        campaign_id, tenant_id, text, n_tokens, who)

    await db.pool().execute(
        """DELETE FROM prompt_versions
            WHERE campaign_id = $1
              AND id NOT IN (SELECT id FROM prompt_versions
                              WHERE campaign_id = $1
                              ORDER BY created_at DESC LIMIT $2)""",
        campaign_id, MAX_VERSIONS)


@router.get("/prompt-versions", response_model=list[PromptVersion])
async def list_prompt_versions(campaign_id: int,
                               user: CurrentUser = Depends(active_user)):
    """Every kept version, newest first, with the full text.

    The text is sent in full rather than as a preview: the point of the list is
    to copy one out or put it back, and a second request per row to fetch what
    is already stored would be a round trip for nothing.
    """
    await assert_campaign_visible(user, campaign_id)
    rows = await db.pool().fetch(
        """SELECT id, campaign_id, instructions, n_tokens, created_by, created_at
             FROM prompt_versions WHERE campaign_id = $1
            ORDER BY created_at DESC""", campaign_id)
    return [PromptVersion(**dict(r)) for r in rows]


@router.post("/prompt-versions/{version_id}/restore",
             response_model=AgentConfigOut)
async def restore_prompt_version(campaign_id: int, version_id: int,
                                 actor: CurrentUser = Depends(editor)):
    """Put an old prompt back, keeping the current one as a new version.

    Nothing is lost by restoring: what is being replaced becomes the newest
    entry in the list, so a restore can be undone by restoring again.
    """
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    row = await db.pool().fetchrow(
        "SELECT instructions FROM prompt_versions "
        " WHERE id = $1 AND campaign_id = $2", version_id, campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such version")

    before = await _get(campaign_id)
    if before.get("instructions") != row["instructions"]:
        await _keep_version(campaign_id, tenant_id, before.get("instructions"),
                            actor.email)
        await db.pool().execute(
            "UPDATE agent_config SET instructions = $2, updated_at = now() "
            " WHERE campaign_id = $1", campaign_id, row["instructions"])
        await audit.record(actor, entity="agent_config",
                           entity_id=before["name"], action="restore_prompt",
                           tenant_id=tenant_id, campaign_id=campaign_id,
                           changes={"instructions": {
                               "from": before.get("instructions"),
                               "to": row["instructions"]}})
    return AgentConfigOut(**await _get(campaign_id))


@router.delete("/prompt-versions/{version_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt_version(campaign_id: int, version_id: int,
                                actor: CurrentUser = Depends(editor)):
    """Remove one version.

    This list is a working set, not a record - config_audit still holds every
    change with both sides of the text, and nothing here can touch that.
    """
    await assert_campaign_visible(actor, campaign_id)
    await db.pool().execute(
        "DELETE FROM prompt_versions WHERE id = $1 AND campaign_id = $2",
        version_id, campaign_id)


@router.get("/routes", response_model=list[CampaignRoute])
async def list_routes(campaign_id: int, user: CurrentUser = Depends(active_user)):
    await assert_campaign_visible(user, campaign_id)
    rows = await db.pool().fetch(
        """SELECT id, campaign_id, did, description, created_at
             FROM campaign_routes WHERE campaign_id = $1 ORDER BY did""",
        campaign_id)
    return [CampaignRoute(**dict(r)) for r in rows]


@router.post("/routes", response_model=CampaignRoute,
             status_code=status.HTTP_201_CREATED)
async def add_route(campaign_id: int, body: CampaignRouteCreate,
                    actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    try:
        row = await db.pool().fetchrow(
            """INSERT INTO campaign_routes (campaign_id, did, description)
               VALUES ($1, $2, $3)
               RETURNING id, campaign_id, did, description, created_at""",
            campaign_id, body.did, body.description)
    except asyncpg.UniqueViolationError:
        # DIDs are unique across every tenant. Say which campaign has it only
        # when the caller is allowed to see that campaign - otherwise the error
        # would leak another client's configuration.
        owner = await db.pool().fetchrow(
            """SELECT c.name, c.tenant_id FROM campaign_routes r
                 JOIN campaigns c ON c.id = r.campaign_id WHERE r.did = $1""",
            body.did)
        where = (f" (used by '{owner['name']}')"
                 if owner and (actor.is_superadmin
                               or owner["tenant_id"] == actor.tenant_id) else "")
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{body.did} is already routed{where}")

    await audit.record(actor, entity="campaign_route", entity_id=body.did,
                       action="create", tenant_id=tenant_id,
                       campaign_id=campaign_id,
                       changes=audit.diff(None, body.model_dump()))
    return CampaignRoute(**dict(row))


@router.delete("/routes/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_route(campaign_id: int, route_id: int,
                       actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    row = await db.pool().fetchrow(
        "SELECT did FROM campaign_routes WHERE id = $1 AND campaign_id = $2",
        route_id, campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "route not found")

    await db.pool().execute("DELETE FROM campaign_routes WHERE id = $1", route_id)
    await audit.record(actor, entity="campaign_route", entity_id=row["did"],
                       action="delete", tenant_id=tenant_id,
                       campaign_id=campaign_id,
                       changes={"did": {"from": row["did"], "to": None}})


@router.get("/audit", response_model=list[AuditEntry])
async def campaign_audit(campaign_id: int,
                         user: CurrentUser = Depends(active_user),
                         limit: int = Query(50, ge=1, le=200)):
    await assert_campaign_visible(user, campaign_id)
    rows = await db.pool().fetch(
        """SELECT a.id, a.entity, a.entity_id, a.action, a.changes, a.created_at,
                  u.email AS user_email
             FROM config_audit a LEFT JOIN users u ON u.id = a.user_id
            WHERE a.campaign_id = $1
            ORDER BY a.created_at DESC LIMIT $2""",
        campaign_id, limit)

    # changes is JSONB; asyncpg hands it back as a string unless a codec is set
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d["changes"], str):
            d["changes"] = json.loads(d["changes"])
        out.append(AuditEntry(**d))
    return out


@router.get("/postbacks", response_model=list[PostbackOut])
async def list_postbacks(campaign_id: int, limit: int = Query(25, ge=1, le=200),
                         failed_only: bool = False,
                         user: CurrentUser = Depends(active_user)):
    """Recent deliveries for this campaign, newest first.

    The log the console shows. A postback that never arrived is invisible from
    everywhere else: the call looks perfectly normal, and only the client
    noticing a gap would ever surface it.
    """
    await assert_campaign_visible(user, campaign_id)
    where = "WHERE campaign_id = $1"
    if failed_only:
        where += " AND status = 'failed'"
    rows = await db.pool().fetch(
        f"""SELECT id, call_id, status, attempts, last_status_code, last_error,
                   next_attempt_at, created_at, sent_at, payload
              FROM call_postbacks {where}
             ORDER BY created_at DESC LIMIT $2""", campaign_id, limit)

    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("payload"), str):
            d["payload"] = json.loads(d["payload"])
        out.append(PostbackOut(**d))
    return out


@router.post("/postbacks/{postback_id}/retry", response_model=PostbackOut)
async def retry_postback(campaign_id: int, postback_id: int,
                         actor: CurrentUser = Depends(editor)):
    """Put a finished row back in the queue.

    Attempts are reset, because someone pressing this has usually just fixed
    the thing that was wrong - keeping the old count would exhaust the retries
    again within seconds and hide whether the fix worked.
    """
    row = await db.pool().fetchrow(
        """UPDATE call_postbacks
              SET status='pending', attempts=0, next_attempt_at=now()
            WHERE id=$1 AND campaign_id=$2
        RETURNING id, call_id, status, attempts, last_status_code, last_error,
                  next_attempt_at, created_at, sent_at, payload""",
        postback_id, campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "postback not found")
    d = dict(row)
    if isinstance(d.get("payload"), str):
        d["payload"] = json.loads(d["payload"])
    return PostbackOut(**d)


# Loaded once, not per request. The encoding is a few megabytes and building it
# on every keystroke would make the counter the slowest thing on the page.
_ENC = None


def _tokens(text: str) -> int:
    global _ENC
    if _ENC is None:
        try:
            import tiktoken
            # o200k_base, which is what gpt-4o and the 4.1 family actually use.
            # Deliberately NOT the cl100k_base that kb.py counts with: this
            # number exists to be compared against what the journal reports as
            # prompt=Ntok and against the bill, and both of those come from the
            # model's own tokeniser. Matching kb.py would make it consistent
            # with the console and wrong about the thing it is measuring.
            _ENC = tiktoken.get_encoding("o200k_base")
        except Exception:
            _ENC = False
    if _ENC is False:
        # Four characters to a token is the usual rule of thumb. Wrong enough
        # that the caller is told so rather than being shown a precise-looking
        # number that is not.
        return max(1, len(text) // 4)
    return len(_ENC.encode(text))


@router.post("/prompt-tokens")
async def prompt_tokens(campaign_id: int, body: PromptTokens,
                        user: CurrentUser = Depends(active_user)):
    """Count the tokens in a piece of prompt text.

    Only the text it is given. The knowledge base and the grounding and transfer
    rules are appended by prompt.py at call time, and counting them here would
    mean writing that assembly a second time - which is the exact drift that
    module exists to prevent. The console says what else is added instead.
    """
    await assert_campaign_visible(user, campaign_id)
    return {"tokens": _tokens(body.text), "exact": _ENC is not False}


# ──────────────────── the dialler's own campaign ids ────────────────────
# What the dialler sends when it asks how many more calls this campaign can
# take. Several per campaign, because the dialler splits one campaign of ours
# across several of theirs and that split is theirs to make.
#
# See routers/dialler_api.py for the endpoint they call, and migration 052.

@router.get("/dialler-ids", response_model=list[DiallerIdOut])
async def list_dialler_ids(campaign_id: int,
                           user: CurrentUser = Depends(active_user)):
    await assert_campaign_visible(user, campaign_id)
    rows = await db.pool().fetch(
        """SELECT id, dialler_campaign_id, created_at
             FROM campaign_dialler_ids
            WHERE campaign_id = $1
            ORDER BY created_at, id""", campaign_id)
    return [DiallerIdOut(**dict(r)) for r in rows]


@router.post("/dialler-ids", response_model=DiallerIdOut,
             status_code=status.HTTP_201_CREATED)
async def add_dialler_id(campaign_id: int, body: DiallerIdCreate,
                         actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)

    # No limit, no answer. The capacity endpoint's whole reply is that number,
    # and a campaign the dialler polls with nothing to report is worse than one
    # it cannot poll at all - it gets an error on every call attempt instead of
    # a clear "this is not set up".
    limit = await db.pool().fetchval(
        "SELECT max_parallel_calls FROM agent_config WHERE campaign_id = $1",
        campaign_id)
    if limit is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Set a concurrent call limit on this campaign first — the dialler "
            "asks how many calls it can take, and without a limit there is no "
            "number to give it. It is on the Limits tab.")

    try:
        row = await db.pool().fetchrow(
            """INSERT INTO campaign_dialler_ids
                   (campaign_id, dialler_campaign_id, created_by)
               VALUES ($1, $2, $3)
            RETURNING id, dialler_campaign_id, created_at""",
            campaign_id, body.dialler_campaign_id, actor.id)
    except asyncpg.UniqueViolationError:
        # Unique across every tenant, not just this one - the capacity request
        # carries this id and nothing else, so two campaigns claiming it would
        # make the answer depend on which row came back first.
        #
        # Deliberately does NOT say which campaign has it. That would tell one
        # client about another's configuration, and a tenant_admin can reach
        # this endpoint.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{body.dialler_campaign_id}' is already registered. Each of the "
            f"dialler's campaign ids can point at only one campaign.")

    await audit.record(actor, entity="campaign", entity_id=campaign_id,
                       action="dialler_id_add", tenant_id=tenant_id,
                       changes={"dialler_campaign_id": body.dialler_campaign_id})
    return DiallerIdOut(**dict(row))


@router.delete("/dialler-ids/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_dialler_id(campaign_id: int, row_id: int,
                            actor: CurrentUser = Depends(editor)):
    tenant_id = await assert_campaign_visible(actor, campaign_id)
    # campaign_id in the WHERE as well as the row id: without it, one campaign's
    # id could be deleted through another campaign's URL.
    row = await db.pool().fetchrow(
        """DELETE FROM campaign_dialler_ids
            WHERE id = $1 AND campaign_id = $2
        RETURNING dialler_campaign_id""", row_id, campaign_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "that dialler id is not on this campaign")

    await audit.record(actor, entity="campaign", entity_id=campaign_id,
                       action="dialler_id_remove", tenant_id=tenant_id,
                       changes={"dialler_campaign_id": row["dialler_campaign_id"]})


@router.get("/dialler-context-keys", response_model=list[str])
async def dialler_context_keys(campaign_id: int,
                               user: CurrentUser = Depends(active_user)):
    """What the dialler has ACTUALLY been sending on this campaign.

    Read from the last 200 calls rather than from a list in our code, because
    the dialler owns this set and has added fields to it without telling anyone -
    store.set_dialler_context says so in as many words. A hardcoded list would be
    correct on the day it was written and quietly short afterwards.

    The `dialer.` prefix is stripped: it exists so LiveKit's own `sip.` namespace
    cannot collide with ours, and nobody choosing a field should have to know it.

    Empty is a real answer, not an error - a campaign that has taken no calls yet,
    or one whose dialler sends no context at all. The console says so rather than
    offering an empty dropdown with no explanation.
    """
    await assert_campaign_visible(user, campaign_id)
    rows = await db.pool().fetch(
        """SELECT DISTINCT k
             FROM (SELECT dialer_context
                     FROM calls
                    WHERE campaign_id = $1 AND dialer_context IS NOT NULL
                    ORDER BY id DESC
                    LIMIT 200) recent,
                  jsonb_object_keys(recent.dialer_context) AS k""",
        campaign_id)
    # Deduped AFTER stripping: `dialer.lead_id` and a bare `lead_id` are one key
    # to anyone reading this list, and DISTINCT ran before the prefix came off.
    return sorted({r["k"].split(".", 1)[-1] for r in rows})
