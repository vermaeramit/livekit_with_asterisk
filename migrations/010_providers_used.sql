-- =============================================================================
-- 010 — Which provider actually served the call
--
-- The config says what a campaign is SUPPOSED to use. Nothing recorded what it
-- actually used, so when Sarvam ran out of credits and every call quietly moved
-- to the OpenAI TTS fallback, the only evidence was a resampling line buried in
-- the worker journal. Finding it took twenty minutes, and only because we
-- already suspected the answer.
--
-- A comma-joined set, not a single value: a call can start on the primary and
-- fall back mid-conversation, and both are true. "sarvam" alone means the
-- fallback never fired; "sarvam,openai" means it did.
--
-- Nullable, because calls that end before their first turn have nothing to
-- report - and an empty string there would look like a provider named "".
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE calls
    ADD COLUMN IF NOT EXISTS stt_provider_used TEXT,
    ADD COLUMN IF NOT EXISTS llm_provider_used TEXT,
    ADD COLUMN IF NOT EXISTS tts_provider_used TEXT;

-- Finding "every call that fell back" is the question this column exists to
-- answer, and it is asked across a time range. A comma in the value is the
-- marker, so the index is on the calls that have one.
CREATE INDEX IF NOT EXISTS calls_fallback_idx
    ON calls (tenant_id, started_at DESC)
 WHERE stt_provider_used LIKE '%,%'
    OR llm_provider_used LIKE '%,%'
    OR tts_provider_used LIKE '%,%';

COMMIT;
