from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ───────────────────────────── auth ─────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None
    role: str
    tenant_id: int | None
    tenant_name: str | None = None
    last_login_at: datetime | None = None


# ───────────────────────────── calls ─────────────────────────────

class CallListItem(BaseModel):
    id: int
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    caller: str | None
    callee: str | None
    language: str | None
    end_reason: str | None
    limit_hit: str | None
    transferred_to: str | None
    turn_count: int | None
    campaign_id: int | None
    campaign_name: str | None = None
    tenant_id: int | None = None


class CallListResponse(BaseModel):
    items: list[CallListItem]
    total: int
    page: int
    page_size: int


class TurnOut(BaseModel):
    seq: int
    role: str
    text: str | None
    ts: datetime
    eou_ms: int | None
    stt_ms: int | None
    llm_ttft_ms: int | None
    tts_ttfb_ms: int | None
    total_ms: int | None
    interrupted: bool
    kb_chunk_ids: list[int] | None = None
    kb_scores: list[float] | None = None


class CallUsage(BaseModel):
    llm_prompt_tokens: int | None
    llm_prompt_cached_tokens: int | None
    llm_completion_tokens: int | None
    tts_characters: int | None
    tts_audio_seconds: float | None
    stt_audio_seconds: float | None


class CallDetail(CallListItem):
    room_name: str | None
    sip_call_id: str | None
    outcome: str | None
    transfer_reason: str | None
    recording_path: str | None
    usage: CallUsage
    turns: list[TurnOut]
