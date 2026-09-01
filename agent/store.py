"""Postgres-backed config + call logging. The admin UI (Step 11) will CRUD these tables."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


@dataclass(frozen=True)
class AgentConfig:
    name: str
    language: str
    greeting: Optional[str]
    instructions: str
    stt_provider: str
    stt_model: Optional[str]
    # NULL = no fallback. Deliberately a column and not a rule - see migration 011.
    stt_fallback_provider: Optional[str]
    llm_provider: str
    llm_model: str
    llm_temperature: float
    tts_provider: str
    tts_model: Optional[str]
    tts_fallback_provider: Optional[str]
    tts_voice: Optional[str]
    allow_interrupt: bool
    max_turns: int
    max_duration_sec: int
    max_prompt_tokens: int
    limit_message: Optional[str]
    kb_enabled: bool
    kb_top_k: int
    kb_min_score: float
    kb_inline_max_tokens: int
    kb_summary: Optional[str]
    transfer_enabled: bool
    transfer_to: str
    transfer_message: Optional[str]
    # Ask the caller before handing over, so "no, wait" can still stop it.
    # Enforced by state in the agent, never by trusting the model to say it has
    # asked - see transfer_to_human.
    transfer_confirm: bool
    transfer_confirm_message: Optional[str]
    # NULL = no silence handling on this campaign. The array's LENGTH is the
    # number of attempts; there is no separate count, because two fields that
    # must agree eventually do not. The last line is spoken and then the call
    # ends, so it is the one written as a goodbye.
    silence_timeout_sec: Optional[int]
    silence_prompts: Optional[list]
    # Written by the model when the conversation is finished. Stripped before
    # TTS and never spoken.
    end_call_marker: str
    # Soniox endpointing, NULL = the provider's own defaults. See
    # migration 017 for why these are per-campaign and not constants.
    stt_endpoint_level: Optional[int]
    stt_endpoint_sensitivity: Optional[float]
    # Appended to the end of the prompt, once per call. Off by default - see
    # migration 023. Read here because _as_config only keeps declared fields:
    # a column the dataclass has not heard of is silently dropped.
    prompt_datetime: bool
    prompt_timezone: str
    # Spoken while the knowledge base is being searched. NULL = say nothing.
    kb_filler_message: Optional[str]
    # Where the call's result goes afterwards. Only what the AGENT needs is
    # here: it extracts and stores, it never delivers. The url, auth and retry
    # settings are read by admin-api at send time, so changing them fixes calls
    # that are still queued.
    postback_enabled: bool
    postback_fields: Optional[list]
    postback_include_transcript: bool
    # false = send the extracted fields alone, flat. See migration 025.
    postback_full_payload: bool
    # NULL = no marker-driven handoff on this campaign. The tool still works.
    transfer_marker: Optional[str]
    # When a dialler is chosen, the transfer target is built from the CAMPAIGN
    # rather than read from transfer_to - see migration 033 and _transfer_target.
    transfer_dialler_id: Optional[int]
    transfer_extension: Optional[str]
    # Spoken with the greeting on every call. Recording is unconditional in the
    # dialplan, so this is the notice that makes keeping it lawful - not a
    # per-campaign preference. NOT NULL in the schema for that reason.
    recording_disclosure: str
    # Added by migration 001 and backfilled. Carried here so start_call() can
    # stamp every call with its tenant - without it a call is invisible to the
    # client it belongs to, and only a superadmin ever sees it.
    campaign_id: Optional[int]


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            os.environ["DATABASE_URL"], min_size=1, max_size=4, command_timeout=5
        )
    return _pool


async def close():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


class CampaignUnavailable(Exception):
    """The dialled number maps to a campaign that must not take this call.

    Distinct from a missing config on purpose. A missing config is a deployment
    fault and should be loud; this is a normal, expected state - a suspended
    client or a paused campaign - and the caller deserves a spoken answer rather
    than a phone that rings until it gives up.
    """


def _as_config(row) -> AgentConfig:
    return AgentConfig(**{f.name: row[f.name] for f in fields(AgentConfig)})


async def load_config(name: str = "default") -> AgentConfig:
    row = await (await pool()).fetchrow(
        "SELECT * FROM agent_config WHERE name = $1 AND enabled", name
    )
    if row is None:
        raise RuntimeError(f"agent_config '{name}' missing or disabled")
    return _as_config(row)


async def load_config_for_did(did: str) -> Optional[AgentConfig]:
    """Resolve the dialled number to a campaign's config.

    -> None when the number is not mapped, so the caller can fall back.
    -> raises CampaignUnavailable when it IS mapped but the campaign is disabled
       or the client is suspended. Those two must not be conflated: falling back
       to a default config for a suspended client would answer calls that were
       deliberately turned off.
    """
    row = await (await pool()).fetchrow(
        """SELECT ac.*, cam.enabled AS campaign_enabled, cam.name AS campaign_name,
                  t.status AS tenant_status, t.name AS tenant_name
             FROM campaign_routes r
             JOIN campaigns cam ON cam.id = r.campaign_id
             JOIN tenants   t   ON t.id   = cam.tenant_id
             JOIN agent_config ac ON ac.campaign_id = cam.id
            WHERE r.did = $1 AND ac.enabled
            ORDER BY ac.id LIMIT 1""",
        did,
    )
    if row is None:
        return None
    if not row["campaign_enabled"]:
        raise CampaignUnavailable(
            f"campaign '{row['campaign_name']}' is disabled")
    if row["tenant_status"] != "active":
        raise CampaignUnavailable(
            f"client '{row['tenant_name']}' is {row['tenant_status']}")
    return _as_config(row)


class ProviderKeyMissing(Exception):
    """The campaign has no usable key for a provider it needs.

    Sibling of CampaignUnavailable, and handled the same way: the caller gets a
    human rather than a call answered on somebody else's provider account. There
    is deliberately no fallback to the platform keys in .env - falling back would
    keep the calls running and the bill arriving, with nothing to notice.
    """


async def load_provider_keys(campaign_id: int) -> dict[str, str]:
    """-> {'openai': '...', 'sarvam': '...'} for this campaign.

    Resolution is campaign override first, then the client's default. The
    ORDER BY does that: campaign_id NULLS LAST puts the override ahead of the
    inherited row, and the dict comprehension keeps the first of each provider.

    Decryption happens here rather than in the caller so a plaintext key exists
    only inside the plugin constructors that need it.
    """
    import crypto

    rows = await (await pool()).fetch(
        """SELECT pk.provider, pk.key_enc
             FROM campaigns c
             JOIN provider_keys pk
               ON pk.tenant_id = c.tenant_id
              AND (pk.campaign_id = c.id OR pk.campaign_id IS NULL)
            WHERE c.id = $1
            ORDER BY pk.provider, pk.campaign_id NULLS LAST""",
        campaign_id,
    )
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(r["provider"], crypto.decrypt(r["key_enc"]))
    return out


async def load_tools(campaign_id: int) -> list[dict]:
    """Enabled tools for this campaign, auth values decrypted.

    Decrypted here for the same reason provider keys are: the plaintext should
    exist only where it is used, and the only user is the HTTP request itself.
    """
    import crypto

    rows = await (await pool()).fetch(
        """SELECT id, name, description, parameters, method, url, headers,
                  auth_header, auth_value_enc, body_template, timeout_ms,
                  max_response_bytes, response_path, filler_message,
                  error_messages, keep_response
             FROM campaign_tools
            WHERE campaign_id = $1 AND enabled
            ORDER BY name""",
        campaign_id,
    )
    out = []
    for r in rows:
        spec = dict(r)
        enc = spec.pop("auth_value_enc", None)
        # A tool whose secret cannot be decrypted is offered WITHOUT it rather
        # than dropped: the request then fails with a 401 that lands in
        # tool_invocations, which says far more than a tool that silently
        # stopped existing.
        if enc:
            try:
                spec["auth_value"] = crypto.decrypt(enc)
            except Exception:
                spec["auth_value"] = None
        # asyncpg hands JSONB back as text unless a codec is registered, and
        # none is. Forgetting a column here does not fail loudly - the value
        # arrives as a string and whatever reads it quietly does the wrong
        # thing, which is exactly what happened to error_messages.
        for k in ("parameters", "headers", "error_messages"):
            if isinstance(spec.get(k), str):
                import json
                spec[k] = json.loads(spec[k])
        out.append(spec)
    return out


async def record_gap(call_id: Optional[int], campaign_id: Optional[int], *,
                     kind: str, query: str, best_score: float | None = None,
                     detail: str | None = None) -> None:
    """Write down something the bot could not answer. Never raises.

    Best effort by design, like every other record here: a call must not fail
    because we could not note what it failed to answer.

    tenant_id is read from the campaign rather than passed in. The agent does
    not otherwise care which tenant a call belongs to, and a second source for
    it is a second thing to get wrong.
    """
    q = " ".join((query or "").split())
    if not q or campaign_id is None:
        return
    try:
        await (await pool()).execute(
            """INSERT INTO knowledge_gaps
                   (tenant_id, campaign_id, call_id, kind, query, query_key,
                    best_score, detail)
               SELECT c.tenant_id, $1, $2, $3, $4, lower($4), $5, $6
                 FROM campaigns c WHERE c.id = $1""",
            campaign_id, call_id, kind, q[:500], best_score, detail)
    except Exception:
        import logging
        logging.getLogger("voice-agent").exception(
            "could not record a knowledge gap (%s: %r)", kind, q[:80])


async def record_tool_call(call_id: Optional[int], *, tool_id, name, arguments,
                           status_code=None, error=None, duration_ms=None,
                           url=None, response=None, request=None) -> None:
    """Never let recording a tool call break the call it describes.

    `url` and `request` are both RESOLVED, after placeholder substitution.
    Storing the templates instead would be pointless - they are already on the
    tool. What is worth keeping is what actually went out, because that is where
    a substitution that did not happen, or a template that does not produce
    valid JSON, becomes visible. Neither is visible from `arguments`, which can
    be entirely correct while the request is malformed.
    """
    import json

    try:
        await (await pool()).execute(
            """INSERT INTO tool_invocations
                   (call_id, tool_id, name, arguments, status_code, error,
                    duration_ms, url, response, request)
               VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9,$10)""",
            call_id, tool_id, name, json.dumps(arguments, default=str),
            status_code, error, duration_ms, url, response, request,
        )
    except Exception:
        import logging
        logging.getLogger("voice-agent").exception("tool_invocations write failed")


async def start_call(room_name, caller, callee, config_name, language,
                     campaign_id: Optional[int] = None,
                     sip_call_id: Optional[str] = None) -> int:
    """Record the start of a call.

    tenant_id is derived from the campaign in the same statement rather than
    passed in, so the two can never disagree. It is denormalised on purpose -
    every list and chart in the admin panel filters by tenant.

    sip_call_id is the Call-ID of the Asterisk -> LiveKit leg. Asterisk names the
    recording after the same value, so this column is what joins a call row to
    its audio. The filename is derived from it rather than stored, so a change of
    mount point cannot leave stale paths behind.
    """
    return await (await pool()).fetchval(
        """INSERT INTO calls (room_name, caller, callee, config_name, language,
                              campaign_id, tenant_id, sip_call_id)
           VALUES ($1,$2,$3,$4,$5,$6,
                   (SELECT tenant_id FROM campaigns WHERE id = $6), $7)
           RETURNING id""",
        room_name, caller, callee, config_name, language, campaign_id,
        sip_call_id,
    )


async def set_dialler_context(call_id: int, ctx: dict) -> None:
    """What the dialling system said about this call, verbatim.

    Kept whole rather than split into columns: the dialler owns this set and
    added seven fields without telling anyone. `dialer.lead_id` is the one that
    matters - it joins a call here to a lead in their CRM.
    """
    import json

    await (await pool()).execute(
        "UPDATE calls SET dialer_context = $2::jsonb WHERE id = $1",
        call_id, json.dumps(ctx),
    )


async def end_call_if_open(call_id: int, reason: str, outcome: str) -> None:
    """Close a call only if nothing else already did.

    A job that dies between start_call() and the real shutdown handler - a bad
    voice or model raises while AgentSession is being built - leaves a row with
    no ended_at. Nothing ever closes it, so it sits in the live monitor as a
    stuck call forever and skews every duration average.
    """
    await (await pool()).execute(
        """UPDATE calls
              SET ended_at    = now(),
                  duration_ms = EXTRACT(EPOCH FROM (now() - started_at)) * 1000,
                  end_reason  = $2,
                  outcome     = $3
            WHERE id = $1 AND ended_at IS NULL""",
        call_id, reason, outcome,
    )


async def end_call(call_id: int, reason: str, outcome: str | None = None):
    await (await pool()).execute(
        """UPDATE calls
              SET ended_at    = now(),
                  duration_ms = EXTRACT(EPOCH FROM (now() - started_at)) * 1000,
                  end_reason  = $2,
                  outcome     = $3
            WHERE id = $1""",
        call_id, reason, outcome,
    )


async def end_call_usage(call_id: int, reason: str, limit_hit: str | None,
                         turn_count: int, usage: dict,
                         providers: dict[str, str] | None = None,
                         models: dict[str, str] | None = None):
    """Store per-call usage so expensive calls can be found afterwards.

    Usage is stored, NOT cost - rates change, usage does not. Multiply at query
    time with whatever the rates are today.
    """
    await (await pool()).execute(
        """UPDATE calls SET
               ended_at    = now(),
               duration_ms = EXTRACT(EPOCH FROM (now() - started_at)) * 1000,
               end_reason  = $2,
               limit_hit   = $3,
               turn_count  = $4,
               llm_prompt_tokens        = $5,
               llm_prompt_cached_tokens = $6,
               llm_completion_tokens    = $7,
               tts_characters           = $8,
               tts_audio_seconds        = $9,
               stt_audio_seconds        = $10,
               stt_provider_used        = $11,
               llm_provider_used        = $12,
               tts_provider_used        = $13,
               -- Which model actually ran, not which one the config names
               -- today. Costing reads these, and a campaign moved from
               -- gpt-4.1-mini to gpt-4.1 would otherwise re-price every call
               -- ever made at about five times what it cost.
               llm_model_used           = $14,
               stt_model_used           = $15,
               tts_model_used           = $16
         WHERE id = $1""",
        call_id, reason, limit_hit, turn_count,
        int(usage.get("llm_prompt_tokens", 0)),
        int(usage.get("llm_prompt_cached_tokens", 0)),
        int(usage.get("llm_completion_tokens", 0)),
        int(usage.get("tts_characters_count", 0)),
        float(usage.get("tts_audio_duration", 0)),
        float(usage.get("stt_audio_duration", 0)),
        (providers or {}).get("stt"),
        (providers or {}).get("llm"),
        (providers or {}).get("tts"),
        (models or {}).get("llm"),
        (models or {}).get("stt"),
        (models or {}).get("tts"),
    )

async def log_turn(call_id: int, seq: int, role: str, text: str | None, **t):
    await (await pool()).execute(
        """INSERT INTO turns
             (call_id, seq, role, text, eou_ms, stt_ms, llm_ttft_ms,
              tts_ttfb_ms, total_ms, interrupted, kb_chunk_ids, kb_scores)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
        call_id, seq, role, text,
        t.get("eou_ms"), t.get("stt_ms"), t.get("llm_ttft_ms"),
        t.get("tts_ttfb_ms"), t.get("total_ms"), bool(t.get("interrupted", False)),
        t.get("kb_chunk_ids"), t.get("kb_scores"),
    )


async def load_turns(call_id: int) -> list[dict]:
    """The conversation, for extraction after the call has ended.

    Read back from the database rather than kept in memory during the call. The
    turns are already written there, and holding a second copy for the whole
    call to use once at the end is memory spent on every concurrent call to save
    one query on each.
    """
    rows = await (await pool()).fetch(
        "SELECT seq, role, text, ts FROM turns WHERE call_id = $1 ORDER BY seq",
        call_id)
    return [dict(r) for r in rows]


async def save_postback(call_id: int, campaign_id: Optional[int],
                        payload: dict) -> None:
    """Queue the call's result for delivery. Never raises.

    ON CONFLICT DO NOTHING because call_id is unique: a job that somehow runs
    its shutdown twice must not queue the same call twice, or a client's system
    sees one call as two.

    Delivery is NOT attempted here. This process exits when the call ends, so it
    cannot retry anything - admin-api sweeps the table. Writing the row is the
    part that must not be lost; sending it is the part that can wait.
    """
    import json
    import logging

    try:
        await (await pool()).execute(
            """INSERT INTO call_postbacks (call_id, campaign_id, payload)
               VALUES ($1, $2, $3::jsonb)
               ON CONFLICT (call_id) DO NOTHING""",
            call_id, campaign_id, json.dumps(payload, default=str))
    except Exception:
        logging.getLogger("voice-agent").exception(
            "could not queue the postback for call %s", call_id)


async def load_tool_calls(call_id: int) -> list[dict]:
    """Tool invocations for a call, with whatever response was kept.

    `response` is NULL unless that tool has keep_response set - see migration
    021. The rows are still worth reading either way: the arguments and status
    say what the model asked for and what came back, and extraction can use the
    response only when someone deliberately allowed it.
    """
    import json

    rows = await (await pool()).fetch(
        """SELECT name, arguments, status_code, response, created_at
             FROM tool_invocations
            WHERE call_id = $1 AND error IS NULL
            ORDER BY created_at, id""", call_id)
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("arguments"), str):
            d["arguments"] = json.loads(d["arguments"])
        out.append(d)
    return out
