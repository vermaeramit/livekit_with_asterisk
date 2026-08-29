-- What a call cost.
--
-- Everything needed is already recorded per call - prompt, cached and
-- completion tokens, TTS characters, and STT audio seconds. What was missing is
-- the price of each, and which model actually incurred it.
--
-- Rates are DATA, not code. A price written into a deployment goes stale the
-- day a provider changes it and says nothing, which for a billing figure is the
-- worst way to be wrong: quietly, and in a number people trust.
--
-- Nothing is seeded. An invented price is worse than a blank, because a blank
-- says "set this" and a wrong number says nothing at all.

CREATE TABLE IF NOT EXISTS provider_rates (
    id         BIGSERIAL PRIMARY KEY,
    -- openai | sarvam | soniox | google
    provider   TEXT NOT NULL,
    -- NULL matches any model from that provider. A row naming the model wins,
    -- so a campaign on gpt-4.1 is not priced at gpt-4.1-mini's rate.
    model      TEXT,
    -- llm_input | llm_cached | llm_output | tts_characters | tts_seconds
    -- | stt_seconds
    kind       TEXT NOT NULL,
    -- per_million | per_hour | per_minute | per_unit
    --
    -- Stored as entered rather than normalised to a price-per-token. Providers
    -- quote per million tokens and per audio hour, and a table that reads back
    -- differently from the page it was copied from invites somebody to "fix" it.
    unit       TEXT NOT NULL,
    usd_price  NUMERIC(16,8) NOT NULL CHECK (usd_price >= 0),
    note       TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by BIGINT REFERENCES users(id) ON DELETE SET NULL
);

-- coalesce, because NULL never equals NULL and two "any model" rows for the
-- same kind would both apply.
CREATE UNIQUE INDEX IF NOT EXISTS provider_rates_key_idx
    ON provider_rates (provider, coalesce(model, ''), kind);

-- Which model actually served the call.
--
-- llm_model lives on agent_config, and configs change. Without these, switching
-- a campaign from gpt-4.1-mini to gpt-4.1 would silently re-price every call
-- ever made at roughly five times what it cost.
ALTER TABLE calls
    ADD COLUMN IF NOT EXISTS llm_model_used TEXT,
    ADD COLUMN IF NOT EXISTS stt_model_used TEXT,
    ADD COLUMN IF NOT EXISTS tts_model_used TEXT;

-- Somewhere for the things that belong to the platform rather than to a tenant.
-- The exchange rate is the first; there will be others.
CREATE TABLE IF NOT EXISTS platform_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by BIGINT REFERENCES users(id) ON DELETE SET NULL
);

COMMENT ON TABLE provider_rates IS
    'Per-provider, per-model unit prices in USD. Nothing is seeded on purpose.';
COMMENT ON TABLE platform_settings IS
    'Platform-wide values. usd_to_inr is read when a cost is displayed.';
