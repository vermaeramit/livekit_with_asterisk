from __future__ import annotations

import json
import re
from decimal import Decimal
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (BaseModel, EmailStr, Field, StringConstraints,
                      field_validator, model_validator)

# A role KEY, not one of a fixed set.
#
# This was a Literal of the four seeded names, which was true right up until
# migration 030 made roles rows somebody can create. A Literal would reject
# every new role with a 422 - thrown before the code that looks the role up and
# decides whether the caller may assign it ever runs, so the error would not
# even say what was wrong.
#
# Whether the role EXISTS is checked where that can be answered: users.py looks
# it up, and the database trigger refuses an unknown key outright.
Role = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$",
                                        min_length=2, max_length=40)]
# Kept in step with provider_keys.PROVIDERS and the CHECK constraints in
# migration 011. All three move together or a save fails at the database.
Provider = Literal["openai", "sarvam", "soniox", "openrouter"]

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
    # Only filled in on /auth/me. The console hides what it cannot use; the API
    # refuses it regardless, so this is tidiness rather than a control.
    permissions: list[str] = []
    all_tenants: bool = False
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
    # The dialler API key: whether one exists, its last four characters, and
    # when it was set. Never the key - it is sha256'd in the database and shown
    # once at generation. The hint is here so the console can say WHICH key a
    # client is holding when the dialler team reports one not working.
    api_key_hint: str | None = None
    api_key_set_at: datetime | None = None


class TenantApiKeyCreated(BaseModel):
    """The one and only time the key is returned.

    Generating a new one replaces the old, so the dialler stops working the
    moment this is called - which is the point of a rotation, and worth saying
    out loud in the console before the button is pressed.
    """
    api_key: str
    api_key_hint: str
    api_key_set_at: datetime


# ──────────────────────── the dialler's campaign ids ────────────────────────

class DiallerIdCreate(BaseModel):
    # Theirs, as they will send it. Trimmed rather than rejected for
    # whitespace: this arrives by copy-paste from another team's console.
    dialler_campaign_id: str = Field(min_length=1, max_length=128)

    @field_validator("dialler_campaign_id")
    @classmethod
    def _trim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class DiallerIdOut(BaseModel):
    id: int
    dialler_campaign_id: str
    created_at: datetime


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
# Only the fields the agent actually reads are exposed.
#
# This used to say that stt_provider, llm_provider and tts_provider were columns
# the worker ignored, and that they would come back when the fallback chain was
# wired. STT and TTS came back long ago; the LLM came back in 049. All three are
# read now, and all three have a fallback of their own.
#
# agent_config.enabled is also deliberately absent: load_config() selects
# "WHERE name = $1 AND enabled" and raises when it misses, which makes calls ring
# forever with no visible error. That switch belongs on the campaign, not here.

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _hhmm(day: str, value) -> tuple[int, int]:
    """Parse "09:30" strictly. A time the agent cannot read is a closed day."""
    try:
        hh, _, mm = str(value).partition(":")
        h, m = int(hh), int(mm)
    except (TypeError, ValueError):
        raise ValueError(f"{day}: {value!r} is not a time like 09:30")
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"{day}: {value!r} is not a time like 09:30")
    return h, m


class ServiceCheck(BaseModel):
    name: str
    detail: str
    # Reachable, which is not the same as healthy - the page says so rather
    # than letting a green tick imply more than a TCP connect can prove.
    ok: bool
    # worker | service. Six of one and a handful of the other, and they fail
    # for different reasons.
    kind: str


class DiskUsage(BaseModel):
    name: str
    path: str
    ok: bool
    total_gb: float
    free_gb: float
    free_pct: float


class CampaignSlots(BaseModel):
    campaign: str
    limit: int
    in_use: int


class SystemHealth(BaseModel):
    checks: list[ServiceCheck]
    disks: list[DiskUsage]
    # A fact, not a verdict. Asterisk is native and listens only on UDP, so
    # there is no socket to probe; how long a gap is too long depends on the
    # time of day, and the reader knows that where this endpoint does not.
    last_call_at: datetime | None = None
    postbacks_pending: int = 0
    postbacks_failed: int = 0
    slots: list[CampaignSlots] = Field(default_factory=list)


class ActivityEvent(BaseModel):
    """One thing that happened, from whichever table recorded it."""
    # alert | error | config | postback | tool | login
    kind: str
    # info | warning | critical. Alerts carry their own; the rest are assigned
    # by what the row means - a provider giving up mid-call is always critical,
    # somebody signing in never is.
    severity: str
    at: datetime
    title: str
    detail: str | None = None
    # Who did it, where there is a who. Only config changes and logins have one.
    actor: str | None = None
    # The one extra fact worth a line: an HTTP code, an attempt count, an IP,
    # or which fields a config change touched.
    extra: str | None = None
    campaign_id: int | None = None
    campaign: str | None = None


class ActivityFeed(BaseModel):
    events: list[ActivityEvent]
    # Echoed back so the page can say what window it is showing rather than
    # assuming the one it asked for is the one it got.
    hours: int


class PromptSection(BaseModel):
    """One piece of the assembled prompt, and where it came from.

    The source matters as much as the text: the point of the preview is that
    six tabs contribute to one string, and knowing which tab to go and change
    is most of what somebody wants from it.
    """
    name: str
    source: str
    text: str
    tokens: int


class PromptTool(BaseModel):
    name: str
    # json_schema, not schema: a field called `schema` shadows an attribute on
    # BaseModel and pydantic says so at import time.
    #
    # Pretty-printed, because it is read rather than parsed.
    json_schema: str
    # Counted on the compact form - that is what is billed, not the indented
    # version shown on screen.
    tokens: int


class FinalPrompt(BaseModel):
    """What the model actually receives, assembled by the agent's own code."""
    text: str
    # Everything except the date line. Byte-identical across every call on this
    # campaign, which is what earns OpenAI's prompt cache - so it is worth
    # seeing on its own.
    cached_text: str
    kb_mode: str
    kb_tokens: int
    total_tokens: int
    cached_tokens: int
    sections: list[PromptSection]
    # Sent beside the prompt, not inside it, and paid for on every turn just
    # the same. The agent's own two are not here - see _tool_summary.
    tools: list[PromptTool] = Field(default_factory=list)


class PromptVersion(BaseModel):
    id: int
    campaign_id: int
    # The whole text. The list exists to copy one out or put it back, and a
    # second request per row for something already stored is a round trip for
    # nothing.
    instructions: str
    n_tokens: int | None = None
    created_by: str | None = None
    created_at: datetime


class CopyHours(BaseModel):
    """Which campaign to take transfer hours from."""
    from_campaign_id: int


class AgentConfigOut(BaseModel):
    campaign_id: int
    name: str
    language: str
    greeting: str | None
    # Spoken instead of `greeting` when a placeholder in it has no value -
    # the dialler sends a name on about 5% of calls. NULL = always use
    # `greeting`.
    greeting_fallback: str | None
    instructions: str

    stt_model: str | None
    # Read by the agent since 049. Before that it was a column nobody obeyed:
    # the worker built openai.LLM unconditionally with a hardcoded Gemini leg
    # behind it, billed to the platform rather than the client.
    llm_provider: str
    llm_model: str
    llm_temperature: float
    llm_fallback_provider: str | None = None
    # Required when the fallback provider is set. There is no provider default
    # to reach for the way STT and TTS have one - on a gateway the model name
    # IS the routing.
    llm_fallback_model: str | None = None
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

    # How many calls run at once, and what the rest hear. NULL = unlimited,
    # which is every campaign until somebody sets one. Enforced in the Asterisk
    # dialplan, before the call reaches LiveKit - so a waiting caller costs
    # nothing in STT, TTS or LLM.
    max_parallel_calls: int | None = None
    queue_message: str | None = None
    queue_gap_seconds: int = 8
    queue_max_wait_seconds: int = 90
    queue_timeout_action: str = "human"
    queue_timeout_message: str | None = None
    # Rendered server-side when the message is saved, never sent by the client.
    # Returned so the page can say whether the audio actually exists - a message
    # with no file is a message nobody will hear.
    queue_audio_file: str | None = None
    queue_timeout_audio_file: str | None = None

    transfer_enabled: bool
    transfer_to: str
    transfer_message: str | None
    transfer_confirm: bool
    transfer_confirm_message: str | None
    # With a dialler set the transfer target is built from these rather than
    # from transfer_to - see migration 033.
    transfer_dialler_id: int | None
    transfer_extension: str | None

    # NULL = no silence handling. The array's LENGTH is the number of attempts;
    # the last line is spoken and then the call ends.
    silence_timeout_sec: int | None
    silence_prompts: list[str] | None
    end_call_marker: str
    transfer_marker: str | None
    transfer_hours_enabled: bool = False
    # {"mon": ["09:30", "18:30"], "sun": null} - null or absent means closed.
    transfer_hours: dict | None = None
    transfer_holidays: list = Field(default_factory=list)
    transfer_closed_message: str | None = None

    # Soniox only today. NULL = the provider's defaults.
    stt_endpoint_level: int | None
    stt_endpoint_sensitivity: float | None

    # Whether the agent is told the date and time, and in which zone.
    prompt_datetime: bool
    prompt_timezone: str

    # Spoken while search_knowledge_base runs. None = silence.
    kb_filler_enabled: bool = True
    kb_filler_message: str | None
    stt_context_terms: list[str] = Field(default_factory=list)

    # Things that are wrong but not invalid - see agent_config._warnings.
    # Returned on read as well as on save, so a mismatch that is already there
    # shows the moment somebody opens the page.
    warnings: list[str] = []

    # Where the call's result is sent afterwards. The auth VALUE is never
    # returned - only the four-character hint, exactly like a provider key.
    postback_enabled: bool
    postback_url: str | None
    postback_auth_header: str | None
    postback_auth_value_hint: str | None
    postback_fields: list[dict] | None
    postback_include_transcript: bool
    # false = the extracted fields alone, flat.
    postback_full_payload: bool
    postback_max_attempts: int
    postback_retry_after_sec: int

    recording_disclosure: str

    stt_provider: str
    tts_provider: str
    # NULL = no fallback for that layer. See migration 011 for why this is a
    # stored choice rather than something inferred from which keys exist.
    stt_fallback_provider: str | None
    tts_fallback_provider: str | None

    updated_at: datetime

    @field_validator("llm_temperature", "kb_min_score", "stt_endpoint_sensitivity",
                     mode="before")
    @classmethod
    def _round_real(cls, v):
        # These columns are float4. Postgres hands 0.6 back as
        # 0.6000000238418579, which then shows up verbatim in a number input.
        return round(float(v), 3) if v is not None else v


class AgentConfigUpdate(BaseModel):
    """Every field optional - the editor sends only what changed."""

    language: str | None = Field(default=None, pattern=r"^[a-z]{2}-[A-Z]{2}$")
    greeting: str | None = Field(default=None, max_length=600)
    greeting_fallback: str | None = Field(default=None, max_length=600)
    # Roughly 30,000 tokens. Not a quality judgement and not a model limit -
    # gpt-4.1-mini would take far more. It is a guard against pasting a whole
    # document in by accident, which is the mistake this catches.
    #
    # Raised from 32,000, which was arbitrary and started refusing a real
    # prompt. The number that should govern the length is the token counter on
    # the field, because every token here is paid on EVERY turn of every call.
    # No max_length here on purpose: a Field constraint runs BEFORE the
    # validator below, so pydantic's own "String should have at most N
    # characters" would be the message and the advice would never be reached.
    instructions: str | None = Field(default=None, min_length=1)

    @field_validator("instructions")
    @classmethod
    def _instructions_are_not_a_document(cls, v):
        """Refuse with advice rather than with a number.

        "String should have at most 32000 characters" tells somebody they have
        hit a wall and nothing about the way round it - and there is a good one
        here that is easy to miss.
        """
        if v is not None and len(v) > 120_000:
            raise ValueError(
                f"the prompt is {len(v):,} characters, over the 120,000 limit. "
                "Long reference material belongs in the knowledge base, where "
                "it is looked up only when a caller asks for it - anything in "
                "the prompt is sent on every turn of every call, and paid for "
                "each time.")
        return v

    stt_model: str | None = Field(default=None, max_length=80)
    llm_provider: Provider | None = None
    # 80 was enough when every model was "gpt-4.1-mini". A gateway prefixes the
    # vendor - "google/gemma-4-26b-a4b-it" - so the names got longer.
    llm_model: str | None = Field(default=None, min_length=1, max_length=120)
    llm_fallback_provider: Provider | None = None
    llm_fallback_model: str | None = Field(default=None, max_length=120)
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

    # Send null to go back to unlimited. The PATCH reads with
    # exclude_unset=True, so an absent field means "leave it" and an explicit
    # null means "clear it" - the two are different on purpose here.
    max_parallel_calls: int | None = Field(default=None, ge=0, le=500)
    # Synthesised once when saved, so length is a cost paid at save time and
    # not per call. Still bounded: this is a hold message, not a script.
    queue_message: str | None = Field(default=None, max_length=600)
    queue_gap_seconds: int | None = Field(default=None, ge=1, le=60)
    queue_max_wait_seconds: int | None = Field(default=None, ge=5, le=600)
    queue_timeout_action: str | None = Field(default=None,
                                             pattern=r"^(human|hangup)$")
    queue_timeout_message: str | None = Field(default=None, max_length=600)
    # queue_audio_file and queue_timeout_audio_file are deliberately absent.
    # They are where the render landed, not something a client may set - a
    # client-supplied path here would be a path Asterisk then plays.

    transfer_enabled: bool | None = None
    transfer_to: str | None = Field(default=None, max_length=200)
    transfer_message: str | None = Field(default=None, max_length=600)
    transfer_confirm: bool | None = None
    transfer_dialler_id: int | None = None
    transfer_extension: str | None = Field(default=None, max_length=40,
                                           pattern=r"^[0-9A-Za-z._-]*$")
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

    # ---- when a human is there to take the handoff ----
    transfer_hours_enabled: bool | None = None
    transfer_hours: dict | None = None
    transfer_holidays: list | None = None
    transfer_closed_message: str | None = Field(default=None, max_length=500)

    @field_validator("transfer_hours")
    @classmethod
    def _hours_are_a_week(cls, v):
        """Seven known days, "HH:MM" to "HH:MM", open before close.

        Validated here rather than trusted from the form because this is read
        by the AGENT, on a live call, at the moment somebody has asked for a
        person. A malformed row there closes a day silently - which is a
        support ticket that starts "transfers stopped working" and contains no
        error anywhere.
        """
        if v is None:
            return v
        clean: dict = {}
        for day, window in v.items():
            if day not in _DAYS:
                raise ValueError(f"{day!r} is not a day of the week")
            if window in (None, [], ()):
                clean[day] = None
                continue
            if not isinstance(window, (list, tuple)) or len(window) != 2:
                raise ValueError(f"{day}: expected an open and a close time")
            start, end = (_hhmm(day, t) for t in window)
            if start >= end:
                # Not a night shift - the form cannot express one, so this is
                # a typo, and accepting it would close the day.
                raise ValueError(
                    f"{day}: {window[0]} is not before {window[1]}")
            clean[day] = [window[0], window[1]]
        return clean

    @field_validator("transfer_holidays")
    @classmethod
    def _holidays_are_dates(cls, v):
        if v is None:
            return v
        clean, seen = [], set()
        for item in v:
            if not isinstance(item, dict) or "date" not in item:
                raise ValueError("each holiday needs a date")
            try:
                day = date.fromisoformat(str(item["date"]))
            except ValueError:
                raise ValueError(f"{item['date']!r} is not a date (YYYY-MM-DD)")
            if day in seen:
                continue
            seen.add(day)
            clean.append({"date": day.isoformat(),
                          "label": str(item.get("label") or "")[:60]})
        # Sorted so the console shows them in order however they were added,
        # and so two campaigns with the same holidays compare equal.
        return sorted(clean, key=lambda h: h["date"])
    # The provider's own limits - outside them is a 400 on the first
    # utterance of a live call.
    stt_endpoint_level: int | None = Field(default=None, ge=0, le=3)
    stt_endpoint_sensitivity: float | None = Field(default=None, ge=-1.0, le=1.0)

    prompt_datetime: bool | None = None
    prompt_timezone: str | None = Field(default=None, max_length=64)
    # Short on purpose. It has to finish before the search does, or the caller
    # hears a sentence about waiting and then waits anyway.
    kb_filler_enabled: bool | None = None
    kb_filler_message: str | None = Field(default=None, max_length=200)

    stt_context_terms: list[str] | None = None

    @field_validator("stt_context_terms")
    @classmethod
    def _terms_are_clean(cls, v):
        """De-duplicated, trimmed and capped.

        The same cleaning runs in the agent as well, and that is not
        duplication for its own sake: this is the console's door, and the agent
        guards the wire. A row edited in psql never passes through here.

        The cap is ours and conservative. An oversized context does not degrade
        gracefully - it fails the STT handshake, which takes the call with it.
        """
        if v is None:
            return v
        out: list[str] = []
        seen: set[str] = set()
        for term in v:
            t = " ".join(str(term).split())[:60].strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        if len(out) > 150:
            raise ValueError(
                f"{len(out)} terms is more than the 150 this sends to the "
                "speech recogniser. Keep the ones callers actually say.")
        return out

    @field_validator("prompt_timezone")
    @classmethod
    def _known_timezone(cls, v: str | None) -> str | None:
        # Checked here rather than left to the agent. A name it cannot resolve
        # falls back to +05:30 and keeps answering - so a typo would not raise
        # anything, it would just tell every caller the wrong time in a zone
        # nobody chose. Better to refuse it while somebody is looking.
        if v is None or not v.strip():
            return v
        import zoneinfo
        try:
            zoneinfo.ZoneInfo(v)
        except Exception:
            raise ValueError(
                f"unknown timezone {v!r} - use an IANA name such as Asia/Kolkata")
        return v

    postback_enabled: bool | None = None
    postback_url: str | None = Field(default=None, max_length=2000)
    postback_auth_header: str | None = Field(default=None, max_length=100)
    # Write-only. None = leave the stored secret alone, "" = clear it.
    postback_auth_value: str | None = Field(default=None, max_length=2000)
    postback_fields: list[dict] | None = None
    postback_include_transcript: bool | None = None
    postback_full_payload: bool | None = None
    postback_max_attempts: int | None = Field(default=None, ge=1, le=20)
    postback_retry_after_sec: int | None = Field(default=None, ge=10, le=3600)

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

class KbSearchIn(BaseModel):
    # English, and short. The agent's tool asks for English because it was
    # measured on this corpus: an English query scores 0.44-0.48 where the same
    # question in raw Devanagari scores 0.13-0.20 and ranks the wrong chunk.
    # A tester that encourages the other thing would teach the wrong lesson.
    query: str = Field(min_length=1, max_length=400)


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


class KbSourceIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000,
                     pattern=r"^https?://")
    title: str | None = Field(default=None, max_length=200)


class KbSourceOut(BaseModel):
    id: int
    campaign_id: int | None
    url: str
    title: str | None = None
    last_fetched_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    page_count: int = 0
    # [{"name": "New Prices Oil & Consummables", "why": "no readable text (3 images)"}]
    # Names rather than a count, because the useful sentence is which page the
    # agent cannot read - not how many.
    skipped: list = Field(default_factory=list)
    document_count: int = 0
    enabled_count: int = 0
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
                    "no_calls", "stale_calls", "provider_errors",
                    "postback_failures", "postback_missing"]


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


class DiallerIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    # Must match a section in iax.conf exactly. Asterisk dials
    # IAX2/<peer>/<extension>, so a typo here fails at the last step - after the
    # caller has already been told to hold.
    peer: str = Field(min_length=2, max_length=80,
                      pattern=r"^[A-Za-z0-9_.-]+$")
    description: str | None = Field(default=None, max_length=300)
    active: bool = True

    # ---- credentials, all optional together ----
    #
    # All four empty = the peer is a hand-written section in iax.conf and this
    # row only names it. Filled in = Asterisk reads them over ODBC and the peer
    # does not exist in any file.
    host: str | None = Field(default=None, max_length=120,
                             pattern=r"^[A-Za-z0-9_.-]+$")
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=80)
    # None on an update means LEAVE IT ALONE. The console never receives the
    # secret, so it cannot send it back, and a form that submits without
    # retyping it must not blank it.
    secret: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _credentials_are_all_or_nothing(self):
        """A host with no secret is worse than no host at all.

        It becomes a peer that fails authentication at the far end, which reads
        like the dialler being down and gets escalated to the wrong team. The
        secret is exempt on update, where absent means unchanged - the router
        checks that against what is already stored.
        """
        if self.host and not self.username:
            self.username = self.peer
        if self.username and not self.host:
            raise ValueError("a username needs a host to send it to")
        if self.port and not self.host:
            raise ValueError("a port needs a host")
        return self


class DiallerOut(BaseModel):
    id: int
    name: str
    peer: str
    description: str | None = None
    active: bool
    campaign_count: int = 0
    updated_at: datetime

    host: str | None = None
    port: int | None = None
    username: str | None = None
    # Whether a secret is stored, and nothing about it.
    #
    # Provider keys return a four-character hint, which is right for a 40
    # character token nobody can guess from four. A trunk password is short -
    # the one in front of us is eight characters and equal to the username - so
    # four would be half of it. There is no version of this the browser needs.
    has_secret: bool = False


class PermissionOut(BaseModel):
    """One thing a role may be allowed to do.

    Served from the backend so the roles page cannot offer a permission that
    guards nothing - the list and the enforcement come from the same file.
    """
    key: str
    group: str
    label: str
    description: str


class RoleIn(BaseModel):
    key: str = Field(min_length=2, max_length=40,
                     pattern="^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=2, max_length=60)
    description: str | None = Field(default=None, max_length=300)
    # Sees every client. Held apart from the permission list on purpose - see
    # deps.CurrentUser.
    all_tenants: bool = False
    permissions: list[str] = []


class RoleOut(BaseModel):
    id: int
    key: str
    name: str
    description: str | None = None
    all_tenants: bool
    # Cannot be edited or deleted.
    builtin: bool
    permissions: list[str] = []
    user_count: int = 0
    updated_at: datetime


class AnalyticsCost(BaseModel):
    """What the calls in this window cost, and how much of it we can see.

    `priced_calls` and `unpriced_calls` are not decoration. If half the calls
    have no rate for their provider, the total is half the truth - and a total
    that is quietly short is worse than no total, because it will be believed.
    """
    currency: str
    total: float
    per_call: float
    # Total cost over total minutes, NOT the average of each call's rate.
    # Averaging rates over-weights the short calls, and a budget is written
    # against the blended figure.
    per_minute_avg: float
    # The worst rate among calls long enough to mean anything. A ten-second
    # call carries a whole greeting and amortises it over nothing, so without a
    # floor this metric just finds the shortest call every time.
    per_minute_max: float | None = None
    per_minute_max_call_id: int | None = None
    per_minute_max_floor_sec: int
    priced_calls: int
    unpriced_calls: int


class AnalyticsSummary(BaseModel):
    calls: int
    transferred: int
    limit_hit: int
    errors: int
    total_duration_ms: int
    # Average handle time, over calls that have a duration. The old
    # avg_duration_ms divided by EVERY call including those that never
    # connected, which understated it.
    avg_duration_ms: int | None
    max_duration_ms: int | None = None
    longest_call_id: int | None = None
    cost: AnalyticsCost | None = None
    total_turns: int
    # None when the caller may not see usage - see permissions.usage.read.
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    completion_tokens: int | None = None
    tts_characters: int | None = None
    latency: Percentiles
    split: LatencySplit
    end_reasons: dict[str, int]


class TimeBucket(BaseModel):
    bucket: datetime
    calls: int
    transferred: int
    limit_hit: int
    # None when the caller may not see usage.
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
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
    # NULL = not refused. Otherwise why: 'closed' or 'holiday'. The caller
    # asked for a person and there was nobody to give them.
    transfer_refused: str | None = None
    # Characters of transcript per language the STT identified, e.g.
    # {"hi": 812, "en": 233}. NULL before this existed, and for providers that
    # do not identify languages.
    detected_languages: dict[str, int] | None = None
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


class PromptTokens(BaseModel):
    """Text to count. Capped well above any sane prompt."""
    text: str = Field(default="", max_length=200_000)


_RATE_KINDS = ("llm_input", "llm_cached", "llm_output",
               "tts_characters", "tts_seconds", "stt_seconds")
_RATE_UNITS = ("per_million", "per_hour", "per_minute", "per_unit")


class ProviderRateIn(BaseModel):
    """One price, as the provider quotes it."""
    provider: str = Field(min_length=2, max_length=40)
    # None means "any model from this provider". A row naming the model wins.
    model: str | None = Field(default=None, max_length=80)
    kind: Literal[_RATE_KINDS]  # type: ignore[valid-type]
    unit: Literal[_RATE_UNITS]  # type: ignore[valid-type]
    price: Decimal = Field(ge=0, max_digits=16, decimal_places=8)
    # What the provider bills in. Sarvam charges rupees and always will.
    currency: Literal['USD', 'INR'] = 'USD'
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _unit_suits_the_kind(self):
        """A token price per hour is not a typo anybody catches later.

        It would simply produce a cost three thousand times out, on a page
        nobody has a second opinion about.
        """
        time_based = self.kind in ("tts_seconds", "stt_seconds")
        time_units = self.unit in ("per_hour", "per_minute")
        if time_based and not time_units and self.unit != "per_unit":
            raise ValueError(
                f"{self.kind} is measured in seconds - price it per_hour, "
                "per_minute or per_unit")
        if not time_based and time_units:
            raise ValueError(
                f"{self.kind} is counted, not timed - price it per_million or "
                "per_unit")
        return self


class ProviderRateOut(BaseModel):
    id: int
    provider: str
    model: str | None
    kind: str
    unit: str
    price: Decimal
    currency: str
    note: str | None = None
    updated_at: datetime
    updated_by_email: str | None = None


class PlatformSetting(BaseModel):
    value: Decimal = Field(gt=0, max_digits=16, decimal_places=6)


class CallCost(BaseModel):
    """What a call cost, and what could not be priced.

    `priced` false means no leg had a rate at all - the console must show that
    differently from a genuine zero, because a confident 0.00 reads as free.
    """
    usd: dict[str, float]
    usd_total: float
    # Per minute of call. None when the call has no duration to divide by.
    usd_per_minute: float | None = None
    inr: dict[str, float] | None = None
    inr_total: float | None = None
    inr_per_minute: float | None = None
    usd_to_inr: float | None = None
    # Named, not counted: "add a rate for soniox / stt_seconds" is a job.
    missing_rates: list[str] = []
    priced: bool
    # Everything that makes the figure less than exact, written out. A bare
    # "approximate" tells a reader to distrust the number without telling them
    # how far, which is the least useful thing it could say.
    caveats: list[str] = []


class KnowledgeGapOut(BaseModel):
    """One QUESTION the bot could not answer, however many times it was asked.

    The rows behind this are one per occurrence - each tied to a call, so it can
    be listened to. They are grouped before they get here because the unit of
    work is the question: twenty callers asking the same thing is one document
    to write, not twenty.
    """
    tenant_id: int
    tenant_name: str | None = None
    campaign_id: int | None = None
    campaign_name: str | None = None
    # kb_miss | kb_weak | tool_failed
    kind: str
    # The most recent spelling of it. This is the field somebody reads to decide
    # what to write.
    query: str
    # Lowercased and collapsed. Identifies the group when acknowledging.
    query_key: str
    detail: str | None = None
    occurrences: int
    # What is still open. A group half handled still shows, with this smaller.
    open_occurrences: int
    first_seen: datetime
    last_seen: datetime
    # The best we managed on a kb_weak - so "we answered from a 0.31 match" is
    # visible rather than being counted as a success.
    worst_score: float | None = None
    # A handful, newest first. Enough to go and listen to.
    call_ids: list[int] = []
    acknowledged_at: datetime | None = None
    acknowledged_by_email: str | None = None
    note: str | None = None


class GapAcknowledge(BaseModel):
    """Acknowledge every open occurrence of one question."""
    campaign_id: int | None = None
    kind: str = Field(pattern="^[a-z_]{3,20}$")
    query_key: str = Field(min_length=1, max_length=500)
    # What was done about it. Read by whoever finds it open again.
    note: str | None = Field(default=None, max_length=1000)


class ToolInvocationOut(BaseModel):
    """One HTTP tool call the agent made during a call.

    `arguments` is what the MODEL decided to send, which is the field worth
    reading: a tool that "did not work" is usually a tool the model called with
    the wrong argument, and that is invisible from the transcript alone.

    A successful response body is stored only when the campaign asked for it.
    A client API answers with customer data, and keeping it by default would put
    personal records in a table nobody thinks of as holding them.

    An ERROR body is always kept and always returned. It is not customer data -
    it is the endpoint saying what it disliked - and it is usually the only
    thing that explains a 4xx.
    """
    id: int
    name: str
    arguments: dict | None = None
    # The RESOLVED url. Arguments alone were not enough: a placeholder written
    # with single braces leaves the arguments looking perfectly correct and
    # sends `?pincode={pin}` to the API.
    url: str | None = None
    # The RESOLVED body actually sent, for the same reason as the url: on call
    # 365 two tools returned 400 because their templates did not produce valid
    # JSON, and the arguments were faultless. NULL for GET.
    request: str | None = None
    # The endpoint's own words. Present on any 4xx/5xx, and on a success only
    # when the campaign chose to keep responses.
    response: str | None = None
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


class RateImport(BaseModel):
    written: list[str]
    # Models the catalogue does not list. Named rather than dropped: an
    # unpriced model shows as zero spend, which reads exactly like a cheap one.
    missing: list[str]
    note: str


class LlmModel(BaseModel):
    id: str
    name: str | None = None
    # Price and context, already formatted, so the choice can be made in the
    # dropdown instead of in another tab. Empty for OpenAI, whose /v1/models
    # carries neither.
    detail: str | None = None
    # Sorted on, not shown. Cheapest first is the reason a gateway is here.
    input_price: float = 0.0


class LlmCatalog(BaseModel):
    provider: str
    models: list[LlmModel] = Field(default_factory=list)


class TtsModel(BaseModel):
    id: str
    name: str | None = None
    # True when the provider is retiring it. Sourced from their documentation,
    # not the API - see routers/provider_keys.py.
    retiring: str | None = None
    voices: list[TtsVoice] = []
    supports_language: bool = True


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatTurnIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # Held by the browser, not by us. A test conversation is a scratchpad and
    # should not appear in the call list beside real ones.
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)


class ChatTurnOut(BaseModel):
    text: str
    # What the agent did on the way to that answer: documents retrieved with
    # their scores, tools called with their arguments. The answer alone tells
    # you what it said; this tells you why.
    steps: list[dict] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    ms: int = 0


class WidgetTurnIn(BaseModel):
    # Generated by the browser and sent back on every turn. Not a login: it
    # only ties one visitor's messages together, and the HISTORY is read from
    # the database rather than trusted from the client - a client that sends
    # its own history can send one where the agent already agreed to something.
    session_id: str = Field(min_length=8, max_length=64,
                            pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(min_length=1, max_length=2000)


class WidgetIn(BaseModel):
    # Exact origins. No wildcards: a wildcard is how one hostname becomes every
    # subdomain somebody else can register.
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    # Its own field rather than a "*" in the list above: a wildcard reads as
    # one more entry, and this is a decision.
    allow_any_origin: bool = False
    enabled: bool = True
    daily_token_cap: int = Field(default=500_000, ge=1000, le=50_000_000)
    welcome: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=80)
    # Ends up in a style attribute on a page we do not control, so it is
    # checked here rather than trusted and escaped later.
    accent_color: str = Field(default="#2563eb", pattern=r"^#[0-9a-fA-F]{6}$")

    @field_validator("allowed_origins")
    @classmethod
    def _origins_are_origins(cls, v):
        """Scheme and host, nothing else.

        A browser sends "https://example.com" with no path and no trailing
        slash, and an entry carrying either never matches - which presents as
        a widget that is simply refused, with the allowlist looking correct.
        """
        out = []
        for raw in v:
            o = str(raw).strip().rstrip("/")
            if not re.fullmatch(r"https?://[A-Za-z0-9.\-]+(?::\d+)?", o):
                raise ValueError(
                    f"{raw!r} is not an origin - it should look like "
                    "https://www.example.com, with no path")
            if o not in out:
                out.append(o)
        return out


class WidgetOut(BaseModel):
    id: int
    campaign_id: int
    public_key: str
    allowed_origins: list[str] = Field(default_factory=list)
    allow_any_origin: bool = False
    enabled: bool
    daily_token_cap: int
    welcome: str | None = None
    title: str | None = None
    accent_color: str = "#2563eb"
    # Whether one is stored, not the bytes. The icon is fetched by its own
    # endpoint; putting it in this response would put a base64 logo in every
    # poll of the widget settings.
    has_icon: bool = False
    # Today's usage against the cap, so the number means something next to it.
    tokens_today: int = 0
    conversations_today: int = 0
    created_at: datetime
    updated_at: datetime


class TtsPreviewIn(BaseModel):
    provider: str
    model: str = Field(max_length=80)
    voice: str = Field(max_length=80)
    language: str = Field(max_length=16)
    # Capped because a preview is synthesised for real, on the campaign's own
    # key. Longer than a sentence or two is somebody using the console as a
    # text-to-speech service.
    text: str = Field(min_length=1, max_length=400)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


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
    # Selected by the query since costing needed them, and dropped here ever
    # since because the model never declared them. "openrouter" alone does not
    # answer which call this was - that provider fronts hundreds of models, and
    # a campaign moved between two of them looks identical without this.
    #
    # The model the campaign was CONFIGURED with, so if a fallback fired this
    # still names the primary - same honest approximation as the provider
    # column beside it.
    stt_model_used: str | None = None
    llm_model_used: str | None = None
    tts_model_used: str | None = None
    recording_path: str | None
    # Resolved from the filesystem on every read. Retention deletes files
    # without touching the database, so a stored flag would go stale.
    recording_available: bool = False
    recording_bytes: int | None = None
    # What the dialler told us about this call: name, product, and its own lead
    # and service-request ids. JSONB because the set is theirs to change - they
    # added seven fields once without telling anyone.
    dialer_context: dict | None = None
    # None when the caller may not see usage. Absent rather than zeroed: a zero
    # is a claim about the call, and the honest answer is that we are not saying.
    usage: CallUsage | None = None
    # What that usage cost, at today's rates. See costing.py for why it is
    # calculated on the way out rather than stamped on the call.
    cost: CallCost | None = None
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
    # Saved, and something it will be asked to do will not work. Carries
    # the sentence rather than a flag: each of these needs different
    # words, and the console shows it in place of the ordinary 'saved'.
    warning: str | None = None


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
    # Spoken only if the tool has not answered within TOOL_FILLER_AFTER_MS.
    # A filler in front of a fast API makes a short pause into a long one.
    filler_message: str | None = Field(default=None, max_length=200)
    # Off keeps the wording and stops saying it. Default true so a tool saved
    # by an older console, or by anything that does not know about this field,
    # keeps behaving as it did.
    filler_enabled: bool = True
    # {"404": "...", "timeout": "...", "default": "..."} - what to tell the
    # model for each outcome. A 404 from a lookup is usually not a failure at
    # all; it means "nothing found here", and the caller deserves to be told
    # that rather than that the system is having trouble.
    error_messages: dict[str, str] | None = None
    # Keep what this endpoint answers, so extraction can read values the
    # caller was never told - a dealer code the agent read out only by name.
    # Off by default and decided PER TOOL: a dealer list is business data, and
    # the next endpoint might answer with a phone number and an address.
    keep_response: bool = False
    enabled: bool = True

    @field_validator("error_messages")
    @classmethod
    def _known_outcomes(cls, v: dict | None) -> dict | None:
        if not v:
            return None
        out = {}
        for key, line in v.items():
            k = str(key).strip().lower()
            if not re.fullmatch(r"[1-5]\d\d|timeout|default", k):
                raise ValueError(
                    f"'{key}' is not an outcome. Use an HTTP status like 404, "
                    "or the words 'timeout' or 'default'.")
            line = (line or "").strip()
            if not line:
                continue        # blank means "no special wording for this one"
            if len(line) > 400:
                raise ValueError("each message must be 400 characters or fewer")
            out[k] = line
        return out or None

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


class ToolWrite(ToolBase):
    """Everything that rejects a BADLY WRITTEN tool, and nothing that reads one.

    These checks used to live on ToolBase, which ToolOut inherits - so the
    moment one of them started rejecting a template that was already saved, the
    tools page went blank. A rule meant to stop a bad tool being written had
    closed the only page from which it could be fixed.

    Validation that refuses input must never sit where output passes through it.
    The stored value may be wrong; that is exactly when you need to see it.
    """

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

        # A JSON body must still be JSON once the placeholders are filled.
        #
        # Call 365 lost three tool calls to 400s and two were this: `price` was
        # missing a comma after "city", and neither the arguments nor the URL
        # showed it - the model had chosen perfectly good values and the request
        # was malformed on the way out. Caught here, it never reaches a caller.
        body = (self.body_template or "").strip()
        if body.startswith(("{", "[")):
            # Every placeholder becomes 1, which is valid both bare and inside
            # quotes - so this tests the template's own punctuation and nothing
            # about the values.
            probe = re.sub(r"\{\{\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\}\}", "1", body)
            try:
                json.loads(probe)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"the body is not valid JSON: {e.msg} at line {e.lineno}, "
                    f"column {e.colno}. Placeholders were replaced with 1 to "
                    "check it, so this is the template's own punctuation - "
                    "usually a missing or extra comma.")

            # An unquoted placeholder holding a string cannot survive.
            #
            # `exchange_price` had "month": {{month}} with month declared a
            # string, and the model duly sent "04". JSON numbers may not have a
            # leading zero, so the body became invalid on the one call where it
            # mattered - and looked fine on every one before it.
            props = (self.parameters or {}).get("properties") or {}
            for m in re.finditer(
                    r'(.?)\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}(.?)', body):
                before, key, after = m.group(1), m.group(2), m.group(3)
                if before == '"' and after == '"':
                    continue                    # quoted, always safe
                kind = (props.get(key) or {}).get("type")
                if kind in (None, "string"):
                    raise ValueError(
                        f'the body has {{{{{key}}}}} without quotes, but {key} '
                        f'is a {kind or "string"}. Write "{{{{{key}}}}}" - '
                        "unquoted, a value like 04 or an empty answer makes "
                        "the whole body invalid JSON and the API returns 400.")
        return self


class ToolCreate(ToolWrite):
    auth_value: str | None = Field(default=None, max_length=2000)


class ToolUpdate(ToolWrite):
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


class PostbackOut(BaseModel):
    """One call's delivery to the client's API.

    `payload` is kept and returned in full: "what did we tell them about this
    call" gets asked months later, and re-deriving it from a transcript is not
    an answer. It carries no secret - the auth header is applied at send time
    and never stored in the body.
    """
    id: int
    call_id: int
    # pending | sent | failed | skipped
    status: str
    attempts: int
    last_status_code: int | None = None
    last_error: str | None = None
    next_attempt_at: datetime | None = None
    created_at: datetime
    sent_at: datetime | None = None
    payload: dict


# --- system ------------------------------------------------------------------

class BackupFile(BaseModel):
    name: str
    bytes: int
    at: datetime


class BackupStatus(BaseModel):
    """Read-only view of the nightly database dumps.

    `problem` is the whole point: a page that shows a list of files leaves the
    reader to work out whether that list is healthy. The newest dump's age
    answers it, and a timer that is armed but failing every night looks
    identical to a working one until you check that.
    """
    configured: bool
    problem: str | None = None
    last_run: datetime | None = None
    # ok | failed, from the script's own status file
    last_result: str | None = None
    last_detail: str | None = None
    newest_at: datetime | None = None
    age_hours: float | None = None
    total_bytes: int = 0
    disk_free_bytes: int | None = None
    disk_total_bytes: int | None = None
    files: list[BackupFile] = []
    # Whether anyone has confirmed SECRETS_KEY is stored off this box. Nothing
    # here can verify that; see migration 022.
    secrets_key_ack: "SystemAck | None" = None


class SystemAck(BaseModel):
    """Someone confirming a thing the server cannot check for itself.

    `stale` means the acknowledgement no longer describes reality - the
    fingerprint of what is running has moved since it was given. An
    acknowledgement that outlives its subject is worse than none, because it
    reads as reassurance.
    """
    key: str
    acked_by: str | None = None
    acked_at: datetime | None = None
    stale: bool = False
