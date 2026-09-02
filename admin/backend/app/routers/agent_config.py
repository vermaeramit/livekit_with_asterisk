"""Per-campaign agent configuration.

The workers call store.load_config() inside the job entrypoint, so a save here
takes effect on the next call with no restart and no effect on calls already in
progress.
"""
from __future__ import annotations

import json
import re

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, db, secretlib
from ..deps import CurrentUser, active_user, assert_campaign_visible, require_perm
from ..schemas import (AgentConfigOut, AgentConfigUpdate, AuditEntry,
                       CampaignRoute, CampaignRouteCreate, CopyHours,
                       PostbackOut, PromptTokens)

router = APIRouter(prefix="/campaigns/{campaign_id}", tags=["agent config"])

editor = require_perm("campaign.write")

FIELDS = (
    "language", "greeting", "instructions",
    "stt_provider", "stt_model", "stt_fallback_provider",
    "llm_model", "llm_temperature",
    "tts_provider", "tts_model", "tts_voice", "tts_fallback_provider",
    "allow_interrupt",
    "kb_enabled", "kb_top_k", "kb_min_score", "kb_inline_max_tokens", "kb_summary",
    "kb_filler_enabled", "kb_filler_message",
    "stt_context_terms",
    "max_turns", "max_duration_sec", "max_prompt_tokens", "limit_message",
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
    await db.pool().execute(
        f"UPDATE agent_config SET {sets}, updated_at = now() WHERE campaign_id = $1",
        campaign_id, *values)

    await audit.record(actor, entity="agent_config", entity_id=before["name"],
                       action="update", tenant_id=tenant_id, campaign_id=campaign_id,
                       changes=audit.diff(before, fields))
    return AgentConfigOut(**await _get(campaign_id))


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
