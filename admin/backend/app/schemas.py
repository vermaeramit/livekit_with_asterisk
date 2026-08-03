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
    usage: CallUsage
    turns: list[TurnOut]
