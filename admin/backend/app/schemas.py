from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

Role = Literal["superadmin", "tenant_admin", "agent", "viewer"]
# Kept in step with provider_keys.PROVIDERS and the CHECK constraints in
# migration 011. All three move together or a save fails at the database.
Provider = Literal["openai", "sarvam", "soniox"]

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
    transfer_confirm: bool
    transfer_confirm_message: str | None

    # NULL = no silence handling. The array's LENGTH is the number of attempts;
    # the last line is spoken and then the call ends.
    silence_timeout_sec: int | None
    silence_prompts: list[str] | None
    end_call_marker: str
    transfer_marker: str | None

    recording_disclosure: str

    stt_provider: str
    tts_provider: str
    # NULL = no fallback for that layer. See migration 011 for why this is a
    # stored choice rather than something inferred from which keys exist.
    stt_fallback_provider: str | None
    tts_fallback_provider: str | None

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
    transfer_confirm: bool | None = None
    transfer_confirm_message: str | None = Field(default=None, max_length=600)

    # Under 3s fires while the caller is drawing breath; over 60s the call is
    # already lost. Both ends are product decisions - the database carries the
    # same CHECK.
    silence_timeout_sec: int | None = Field(default=None, ge=3, le=60)
    # One line per attempt. Five is a ceiling on politeness, not on storage.
    silence_prompts: list[str] | None = Field(default=None, max_length=5)
    end_call_marker: str | None = Field(default=None, min_length=2,
                                        max_length=20)
    transfer_marker: str | None = Field(default=None, max_length=20)

    # min_length=1, not Optional. Every call is recorded unconditionally by the
    # dialplan, so a campaign with nothing to say here would be recording callers
    # without telling them. The database carries the same CHECK.
    recording_disclosure: str | None = Field(default=None, min_length=1,
                                             max_length=400)

    stt_provider: Provider | None = None
    tts_provider: Provider | None = None
    stt_fallback_provider: Provider | None = None
    tts_fallback_provider: Provider | None = None

    @field_validator("recording_disclosure")
    @classmethod
    def _not_blank(cls, v: str | None) -> str | None:
        # min_length alone accepts "   ". Postgres rejects it via btrim; catching
        # it here turns a 500 into a readable 422.
        if v is not None and not v.strip():
            raise ValueError("the recording disclosure cannot be blank")
        return v

    @field_validator("silence_prompts")
    @classmethod
    def _prompts_are_spoken(cls, v: list[str] | None) -> list[str] | None:
        """Blank lines are dropped, not stored.

        A blank entry becomes an attempt that says nothing - the caller hears
        the same silence for another timeout and is then hung up on with no
        warning at all. Since the array's length IS the attempt count, an empty
        string is not a small formatting problem; it silently changes what the
        caller experiences.
        """
        if v is None:
            return v
        cleaned = [s.strip() for s in v if s and s.strip()]
        if not cleaned:
            # Empty means "off". Sending [] to clear it is reasonable; storing
            # an empty array would arm the timeout with nothing to say.
            return None
        if any(len(s) > 600 for s in cleaned):
            raise ValueError("each line must be 600 characters or fewer")
        return cleaned

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


class ToolInvocationOut(BaseModel):
    """One HTTP tool call the agent made during a call.

    `arguments` is what the MODEL decided to send, which is the field worth
    reading: a tool that "did not work" is usually a tool the model called with
    the wrong argument, and that is invisible from the transcript alone.

    The response body is deliberately absent - it is not stored. A client API
    answers with customer data, and keeping it here would put personal records
    in a table nobody thinks of as holding them.
    """
    id: int
    name: str
    arguments: dict | None = None
    # The RESOLVED url. Arguments alone were not enough: a placeholder written
    # with single braces leaves the arguments looking perfectly correct and
    # sends `?pincode={pin}` to the API.
    url: str | None = None
    status_code: int | None = None
    duration_ms: int | None = None
    # NULL on success. "timeout" is the one that cost the caller silence.
    error: str | None = None
    created_at: datetime


class ToolActivityItem(ToolInvocationOut):
    """An invocation seen from the tool's side rather than the call's."""
    call_id: int | None = None


class ToolActivityResponse(BaseModel):
    items: list[ToolActivityItem]
    total: int
    page: int
    page_size: int


# --- what a TTS provider actually offers -------------------------------------
# Read from the provider, never held as a list here. A hardcoded copy of
# Soniox's voices had drifted badly: it was the union of two models, so it
# offered Meera - which exists on tts-rt-v1 and not on tts-rt-v2 - and a voice
# the model does not have makes TTS.__init__ raise before the call is answered.

class TtsVoice(BaseModel):
    id: str
    gender: str | None = None
    description: str | None = None


class TtsModel(BaseModel):
    id: str
    name: str | None = None
    # True when the provider is retiring it. Sourced from their documentation,
    # not the API - see routers/provider_keys.py.
    retiring: str | None = None
    voices: list[TtsVoice] = []
    supports_language: bool = True


class TtsCatalog(BaseModel):
    provider: str
    models: list[TtsModel]


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
    # What actually served the call, not what the config asked for. A comma
    # means a fallback fired partway: "sarvam,openai".
    stt_provider_used: str | None = None
    llm_provider_used: str | None = None
    tts_provider_used: str | None = None
    recording_path: str | None
    # Resolved from the filesystem on every read. Retention deletes files
    # without touching the database, so a stored flag would go stale.
    recording_available: bool = False
    recording_bytes: int | None = None
    # What the dialler told us about this call: name, product, and its own lead
    # and service-request ids. JSONB because the set is theirs to change - they
    # added seven fields once without telling anyone.
    dialer_context: dict | None = None
    usage: CallUsage
    turns: list[TurnOut]
    tools: list[ToolInvocationOut] = []


# --- provider keys -----------------------------------------------------------
# Note what is absent: there is no field anywhere here that carries a key back
# to the client. ProviderKeySet is write-only, and everything returned is built
# from the hint.

class ProviderKeySet(BaseModel):
    # No format validation. Providers change their key prefixes, and a regex
    # that rejects a valid new-style key is worse than one that lets a typo
    # through - the live check against the provider catches the typo anyway.
    key: str = Field(min_length=8, max_length=512)


class ProviderKeyOut(BaseModel):
    provider: str
    # campaign | client | none - which key the next call would actually use
    source: str
    hint: str | None
    updated_at: datetime | None


class ProviderKeyWritten(BaseModel):
    provider: str
    hint: str
    message: str
    # The key authenticates but the account cannot pay. Saved anyway - the key
    # is correct - but the console has to say so, or the first anyone hears of
    # it is a caller being handed to a human.
    no_credits: bool = False


# --- campaign tools ----------------------------------------------------------
# auth_value is write-only, like a provider key: it goes in through create or
# update and never comes back out. ToolOut carries a hint instead.

ToolMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


class ToolBase(BaseModel):
    # Matches the model's function-name rules and the CHECK in migration 013.
    # Validated below rather than with Field(pattern=...): pydantic reports that
    # as "String should match pattern '^[a-z][a-z0-9_]{2,47}$'", which says
    # nothing about which field or what to type instead.
    name: str
    # The only thing the model reads when deciding whether to call this. A vague
    # one is the usual reason a tool fires at the wrong moment, or never.
    description: str = Field(min_length=10, max_length=1000)
    parameters: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    method: ToolMethod = "GET"
    url: str = Field(pattern=r"^https?://", max_length=2000)
    headers: dict[str, str] | None = None
    auth_header: str | None = Field(default=None, max_length=100)
    body_template: str | None = Field(default=None, max_length=4000)
    # A tool call happens inside a ~2s turn budget. Past that the caller is
    # listening to silence, which is worse than a tool that failed.
    timeout_ms: int = Field(default=2500, ge=200, le=8000)
    max_response_bytes: int = Field(default=8192, ge=256, le=65536)
    response_path: str | None = Field(default=None, max_length=200)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _callable_name(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,47}", v):
            raise ValueError(
                f"tool name '{v}' is not usable as a function name. Use 3-48 "
                "characters: lowercase letters, digits and underscores, "
                "starting with a letter - e.g. 'dealers_by_pincode'. This is "
                "the name the model calls, and the providers enforce it.")
        return v

    @field_validator("parameters")
    @classmethod
    def _looks_like_schema(cls, v: dict) -> dict:
        # Not full JSON Schema validation - just the shape every provider
        # requires, so a malformed one fails here rather than on a live call.
        if v.get("type") != "object" or not isinstance(v.get("properties"), dict):
            raise ValueError('parameters must be a JSON Schema object with '
                             '"type": "object" and a "properties" map')

        # A name in "required" that is not in "properties" is a schema the model
        # cannot satisfy: it is told the argument is mandatory and never told
        # what it is. Some providers reject it outright, others accept it and
        # the argument simply never arrives.
        missing = [r for r in (v.get("required") or [])
                   if r not in v["properties"]]
        if missing:
            raise ValueError(
                f"'required' lists {', '.join(missing)}, but "
                f"'properties' only defines "
                f"{', '.join(v['properties']) or 'nothing'}. Every required "
                "argument must be described in properties, or the model is "
                "asked for something it was never told about.")
        return v

    @model_validator(mode="after")
    def _placeholders_are_declared(self):
        """Every {{arg}} in the URL or body must be an argument the model has.

        Without this the failure is silent and late: an undeclared placeholder
        substitutes to empty, so the request goes out as `?pincode=` and the
        API answers 400 mid-call. It looks like the client's API is broken.
        """
        declared = set((self.parameters or {}).get("properties") or {})
        used = set()
        for tpl in (self.url, self.body_template):
            if tpl:
                used |= set(re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}",
                                       tpl))
        unknown = sorted(used - declared)
        if unknown:
            raise ValueError(
                f"the URL or body uses {{{{{unknown[0]}}}}}"
                + (f" and {len(unknown) - 1} more" if len(unknown) > 1 else "")
                + f", but parameters only declares "
                f"{', '.join(sorted(declared)) or 'nothing'}. An undeclared "
                "placeholder is replaced with an empty string, so the request "
                "goes out with the value missing.")

        # Single braces around a declared argument name. Nothing substitutes
        # {pin}, so it is sent to the API verbatim - and the API answers with
        # something plausible ("no dealer found for the given pincode") that
        # reads as a data problem rather than a typo. Found exactly that way.
        #
        # Only flagged when the name is a DECLARED argument: a JSON body is full
        # of legitimate braces, and guessing at intent there would reject valid
        # templates.
        for tpl, where in ((self.url, "URL"), (self.body_template, "body")):
            if not tpl:
                continue
            for m in re.finditer(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})", tpl):
                if m.group(1) in declared:
                    raise ValueError(
                        f"the {where} has {{{m.group(1)}}} with single braces. "
                        f"Placeholders need two: {{{{{m.group(1)}}}}}. As "
                        "written it is sent to the API literally.")
        return self


class ToolCreate(ToolBase):
    auth_value: str | None = Field(default=None, max_length=2000)


class ToolUpdate(ToolBase):
    # Omitted means "leave the stored secret alone"; sending "" clears it.
    auth_value: str | None = Field(default=None, max_length=2000)


class ToolOut(ToolBase):
    id: int
    auth_value_hint: str | None
    updated_at: datetime


class ToolTestResult(BaseModel):
    ok: bool
    status_code: int | None
    duration_ms: int
    # Truncated exactly as the agent would truncate it, so what is shown here is
    # what the model would actually receive.
    body: str | None
    error: str | None
    url: str
