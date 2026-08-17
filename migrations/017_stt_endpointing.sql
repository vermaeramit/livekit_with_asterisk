-- =============================================================================
-- 017 — Soniox endpointing, per campaign
--
-- Measured across 144 turns: Soniox averages stt_ms 1067 and eou 1454, against
-- Sarvam's 238 and 950. Soniox is the chosen provider, so the gap is worth
-- attacking rather than arguing with - and stt-rt-v5 exposes three endpointing
-- controls, of which we were sending exactly one.
--
--   max_endpoint_delay_ms          500-3000, default 2000  -> already sent (1500)
--   endpoint_latency_adjustment    0-3,      default 0     -> never sent
--   endpoint_sensitivity           -1.0-1.0, default 0.0   -> never sent
--
-- Soniox's own guidance: set the level first to pick a latency profile, then
-- use sensitivity to fine-tune, and do not combine a high level with negative
-- sensitivity - they cancel out.
--
-- Configurable rather than hardcoded because the right value is a judgement
-- about THIS campaign's callers, not a fact. A higher level returns results
-- sooner but segments longer speech more aggressively, and these are Hinglish
-- sales calls where people speak in long sentences - being cut off mid-thought
-- is worse than waiting.
--
-- NULL = send nothing and let the provider default apply. Only Soniox reads
-- these today; Sarvam has its own knobs, which live in env.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS stt_endpoint_level INTEGER;
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS stt_endpoint_sensitivity REAL;

-- The provider's own limits. Sending anything outside them is a 400 on the
-- first utterance of a live call, which is the worst place to discover it.
ALTER TABLE agent_config
    DROP CONSTRAINT IF EXISTS agent_config_stt_endpoint_level_chk;
ALTER TABLE agent_config
    ADD CONSTRAINT agent_config_stt_endpoint_level_chk
    CHECK (stt_endpoint_level IS NULL OR stt_endpoint_level BETWEEN 0 AND 3);

ALTER TABLE agent_config
    DROP CONSTRAINT IF EXISTS agent_config_stt_endpoint_sens_chk;
ALTER TABLE agent_config
    ADD CONSTRAINT agent_config_stt_endpoint_sens_chk
    CHECK (stt_endpoint_sensitivity IS NULL
           OR stt_endpoint_sensitivity BETWEEN -1.0 AND 1.0);

COMMIT;
