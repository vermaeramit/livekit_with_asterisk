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
    kb_enabled: bool
    kb_top_k: int
    kb_min_score: float
    kb_inline_max_tokens: int
    kb_summary: Optional[str]
    transfer_enabled: bool
    transfer_to: str
    transfer_message: Optional[str]


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


async def load_config(name: str = "default") -> AgentConfig:
    row = await (await pool()).fetchrow(
        "SELECT * FROM agent_config WHERE name = $1 AND enabled", name
    )
    if row is None:
        raise RuntimeError(f"agent_config '{name}' missing or disabled")
    return AgentConfig(**{f.name: row[f.name] for f in fields(AgentConfig)})


async def start_call(room_name, caller, callee, config_name, language) -> int:
    return await (await pool()).fetchval(
        """INSERT INTO calls (room_name, caller, callee, config_name, language)
           VALUES ($1,$2,$3,$4,$5) RETURNING id""",
        room_name, caller, callee, config_name, language,
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
