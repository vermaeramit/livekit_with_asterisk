from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

Role = Literal["superadmin", "tenant_admin", "agent", "viewer"]

# 12 characters is the floor everywhere a password is set, so the rule cannot be
# bypassed by picking a different endpoint.
Password = Annotated[str, Field(min_length=12, max_length=200)]

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")


class _SlugMixin:
    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str) -> str:
        v = v.strip().lower()
        if not SLUG_RE.match(v):
            raise ValueError(
                "slug must be lowercase letters, digits and hyphens, "
                "2-40 characters, not starting or ending with a hyphen"
            )
        return v


# ───────────────────────────── auth ─────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: Password


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
    must_change_password: bool = False
    active: bool = True
    created_at: datetime | None = None


# ───────────────────────────── tenants ─────────────────────────────

class TenantCreate(_SlugMixin, BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=120)


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "suspended"] | None = None


class TenantOut(BaseModel):
    id: int
    slug: str
    name: str
    status: str
    created_at: datetime
    campaign_count: int = 0
    user_count: int = 0
    call_count: int = 0


# ───────────────────────────── campaigns ─────────────────────────────

class CampaignCreate(_SlugMixin, BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    # superadmin only; everyone else gets their own tenant
    tenant_id: int | None = None


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None


class CampaignOut(BaseModel):
    id: int
    tenant_id: int
    tenant_name: str | None = None
    slug: str
    name: str
    description: str | None = None
    enabled: bool
    created_at: datetime | None = None
    call_count: int = 0
    config_name: str | None = None


# ───────────────────────────── routing ─────────────────────────────

class CampaignRouteCreate(BaseModel):
    # Same shape the database CHECK enforces, so a bad number is refused with a
    # readable message instead of a constraint violation.
    did: str = Field(min_length=1, max_length=64, pattern=r"^[0-9A-Za-z+*#._-]+$")
    description: str | None = Field(default=None, max_length=200)


class CampaignRoute(BaseModel):
    id: int
    campaign_id: int
    did: str
    description: str | None
    created_at: datetime


# ───────────────────────────── agent config ─────────────────────────────
# Only the fields the agent actually reads are exposed. stt_provider /
# llm_provider / tts_provider are columns the worker ignores - it constructs
# sarvam.STT, openai.LLM and sarvam.TTS unconditionally - so offering them as
# controls would be a lie. They come back when the fallback chain is wired.
#
# agent_config.enabled is also deliberately absent: load_config() selects
# "WHERE name = $1 AND enabled" and raises when it misses, which makes calls ring
# forever with no visible error. That switch belongs on the campaign, not here.

class AgentConfigOut(BaseModel):
    campaign_id: int
    name: str
    language: str
    greeting: str | None
    instructions: str

    stt_model: str | None
    llm_model: str
    llm_temperature: float
    tts_model: str | None
    tts_voice: str | None
    allow_interrupt: bool

    kb_enabled: bool
    kb_top_k: int
    kb_min_score: float
    kb_inline_max_tokens: int
    kb_summary: str | None

    max_turns: int
    max_duration_sec: int
    max_prompt_tokens: int
    limit_message: str | None

    transfer_enabled: bool
    transfer_to: str
    transfer_message: str | None

    updated_at: datetime

    @field_validator("llm_temperature", "kb_min_score", mode="before")
    @classmethod
    def _round_real(cls, v):
        # These columns are float4. Postgres hands 0.6 back as
        # 0.6000000238418579, which then shows up verbatim in a number input.
        return round(float(v), 3) if v is not None else v


class AgentConfigUpdate(BaseModel):
    """Every field optional - the editor sends only what changed."""

    language: str | None = Field(default=None, pattern=r"^[a-z]{2}-[A-Z]{2}$")
    greeting: str | None = Field(default=None, max_length=600)
    instructions: str | None = Field(default=None, min_length=1, max_length=32000)

    stt_model: str | None = Field(default=None, max_length=80)
    llm_model: str | None = Field(default=None, min_length=1, max_length=80)
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    tts_model: str | None = Field(default=None, max_length=80)
    tts_voice: str | None = Field(default=None, max_length=80)
    allow_interrupt: bool | None = None

    kb_enabled: bool | None = None
    kb_top_k: int | None = Field(default=None, ge=1, le=10)
    kb_min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    # Above roughly 8k the inline KB stops paying for itself - it is prepended to
    # every prompt, so it is billed on every turn of every call.
    kb_inline_max_tokens: int | None = Field(default=None, ge=0, le=16000)
    kb_summary: str | None = Field(default=None, max_length=8000)

    max_turns: int | None = Field(default=None, ge=1, le=300)
    max_duration_sec: int | None = Field(default=None, ge=30, le=7200)
    max_prompt_tokens: int | None = Field(default=None, ge=1000, le=1_000_000)
    limit_message: str | None = Field(default=None, max_length=600)

    transfer_enabled: bool | None = None
    transfer_to: str | None = Field(default=None, max_length=200)
    transfer_message: str | None = Field(default=None, max_length=600)

    @field_validator("transfer_to")
    @classmethod
    def _check_sip_uri(cls, v: str | None) -> str | None:
        # A malformed target is only discovered when a real caller asks for a
        # human and the REFER fails - worth catching at save time.
        if v is None:
            return v
        v = v.strip()
        if not v.startswith("sip:") or "@" not in v:
            raise ValueError("must be a SIP URI, e.g. sip:800@10.130.9.243")
        return v


# ───────────────────────────── knowledge base ─────────────────────────────

class KbDocument(BaseModel):
    id: int
    campaign_id: int | None
    config_name: str
    filename: str
    title: str | None
    page_count: int | None
    chunk_count: int | None
    token_count: int = 0
    language: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class KbIngestResult(BaseModel):
    filename: str
    # created | updated | unchanged | empty
    status: str
    pages: int | None = None
    chunks: int | None = None
    tokens: int | None = None
    error: str | None = None


# ───────────────────────────── alerting ─────────────────────────────

AlertKind = Literal["latency_p95", "error_rate", "transfer_rate", "limit_hits",
                    "no_calls", "stale_calls"]


class AlertRuleOut(BaseModel):
    id: int
    tenant_id: int
    tenant_name: str | None = None
    campaign_id: int | None
    campaign_name: str | None = None
    kind: str
    threshold: float
    window_minutes: int
    min_calls: int
    severity: str
    enabled: bool
    firing: bool
    last_fired_at: datetime | None
    last_checked_at: datetime | None

    @field_validator("threshold", mode="before")
    @classmethod
    def _round_real(cls, v):
        return round(float(v), 2) if v is not None else v


class AlertRuleUpdate(BaseModel):
    threshold: float | None = Field(default=None, ge=0)
    window_minutes: int | None = Field(default=None, ge=5, le=1440)
    min_calls: int | None = Field(default=None, ge=0, le=1000)
    severity: Literal["warning", "critical"] | None = None
    enabled: bool | None = None


class AlertOut(BaseModel):
    id: int
    tenant_id: int
    tenant_name: str | None = None
    campaign_id: int | None
    campaign_name: str | None = None
    kind: str
    severity: str
    message: str
    value: float | None
    threshold: float | None
    # pending | sent | failed | skipped
    delivery: str
    delivery_error: str | None
    created_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by_email: str | None = None

    @field_validator("value", "threshold", mode="before")
    @classmethod
    def _round_real(cls, v):
        return round(float(v), 2) if v is not None else v


class WebhookUpdate(BaseModel):
    # None clears it. http:// is allowed because an internal collector on the
    # LAN is a legitimate target.
    webhook_url: str | None = Field(default=None, max_length=500)

    @field_validator("webhook_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("must be an http:// or https:// URL")
        return v


# ───────────────────────────── live ─────────────────────────────

class LiveCall(BaseModel):
    id: int
    started_at: datetime
    caller: str | None
    callee: str | None
    language: str | None
    campaign_id: int | None
    campaign_name: str | None
    tenant_id: int | None
    elapsed_sec: int
    max_duration_sec: int
    turn_count: int
    last_latency_ms: int | None
    last_text: str | None
    # Almost certainly a worker that died mid-call: the row is open but the
    # elapsed time is past the call's own duration guardrail.
    stale: bool


class LiveSummary(BaseModel):
    calls: list[LiveCall]
    active: int
    stale: int
    verified_capacity: int


# ───────────────────────────── analytics ─────────────────────────────

class Percentiles(BaseModel):
    p50: float | None
    p90: float | None
    p95: float | None
    worst: int | None
    turns: int


class LatencySplit(BaseModel):
    """Median contribution of each stage to a turn.

    Three fields, not four. stt_ms is already inside eou_ms - adding it as a
    fourth slice double-counts, which this project has done once before.
    """
    eou_ms: float | None
    llm_ttft_ms: float | None
    tts_ttfb_ms: float | None


class AnalyticsSummary(BaseModel):
    calls: int
    transferred: int
    limit_hit: int
    errors: int
    total_duration_ms: int
    avg_duration_ms: int | None
    total_turns: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    tts_characters: int
    latency: Percentiles
    split: LatencySplit
    end_reasons: dict[str, int]


class TimeBucket(BaseModel):
    bucket: datetime
    calls: int
    transferred: int
    limit_hit: int
    prompt_tokens: int
    cached_tokens: int
    p50: float | None
    p95: float | None
    eou_ms: float | None
    llm_ttft_ms: float | None
    tts_ttfb_ms: float | None


class AuditEntry(BaseModel):
    id: int
    entity: str
    entity_id: str | None
    action: str
    changes: dict | None
    created_at: datetime
    user_email: str | None = None


# ───────────────────────────── users ─────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=120)
    role: Role
    password: Password
    # superadmin only; a tenant_admin always creates inside its own tenant
    tenant_id: int | None = None
    must_change_password: bool = True


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    role: Role | None = None
    active: bool | None = None


class PasswordReset(BaseModel):
    password: Password
    must_change_password: bool = True


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
    # Resolved from the filesystem on every read. Retention deletes files
    # without touching the database, so a stored flag would go stale.
    recording_available: bool = False
    recording_bytes: int | None = None
    usage: CallUsage
    turns: list[TurnOut]
