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
    llm_provider: str
    llm_model: str
    llm_temperature: float
    tts_provider: str
    tts_model: Optional[str]
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
                         providers: dict[str, str] | None = None):
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
               tts_provider_used        = $13
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
