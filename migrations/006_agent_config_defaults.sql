-- =============================================================================
-- 006 — Repair the agent_config defaults
--
-- The column defaults date from the original schema, when the plan was Google
-- for all three layers:
--
--     llm_model DEFAULT 'gemini-flash-latest'
--     stt_provider / llm_provider / tts_provider DEFAULT 'google'
--
-- The stack has been Sarvam + OpenAI since Step 8, but the defaults were never
-- updated. A campaign created from the admin panel inherits them, so it is born
-- unable to take a call: openai.LLM(model='gemini-flash-latest') returns
-- 404 model_not_found on the first turn.
--
-- That stayed invisible because the only campaign in use predated the panel and
-- had its values set explicitly. It surfaced when the load-test campaign was
-- created through the UI and all ten calls died.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE agent_config ALTER COLUMN stt_provider SET DEFAULT 'sarvam';
ALTER TABLE agent_config ALTER COLUMN stt_model    SET DEFAULT 'saarika:v2.5';
ALTER TABLE agent_config ALTER COLUMN llm_provider SET DEFAULT 'openai';
ALTER TABLE agent_config ALTER COLUMN llm_model    SET DEFAULT 'gpt-4.1-mini';
ALTER TABLE agent_config ALTER COLUMN tts_provider SET DEFAULT 'sarvam';
ALTER TABLE agent_config ALTER COLUMN tts_model    SET DEFAULT 'bulbul:v3';
ALTER TABLE agent_config ALTER COLUMN tts_voice    SET DEFAULT 'anushka';

-- Repair rows that took the stale defaults. Matched on the exact stale values so
-- a deliberately chosen model is never overwritten - someone may genuinely want
-- Gemini once the fallback work makes that a real option.
UPDATE agent_config
   SET llm_provider = 'openai',
       llm_model    = 'gpt-4.1-mini',
       updated_at   = now()
 WHERE llm_model = 'gemini-flash-latest';

UPDATE agent_config
   SET stt_provider = 'sarvam',
       stt_model    = COALESCE(stt_model, 'saarika:v2.5'),
       updated_at   = now()
 WHERE stt_provider = 'google';

UPDATE agent_config
   SET tts_provider = 'sarvam',
       tts_model    = COALESCE(tts_model, 'bulbul:v3'),
       tts_voice    = COALESCE(tts_voice, 'anushka'),
       updated_at   = now()
 WHERE tts_provider = 'google';

DO $$
DECLARE
    broken BIGINT;
BEGIN
    SELECT count(*) INTO broken FROM agent_config
     WHERE llm_model LIKE 'gemini%' OR tts_provider = 'google' OR stt_provider = 'google';
    IF broken > 0 THEN
        RAISE WARNING 'agent_config rows still on the old Google stack: %', broken;
    END IF;
END $$;

COMMIT;
