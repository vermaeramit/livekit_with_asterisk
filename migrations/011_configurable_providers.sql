-- =============================================================================
-- 011 — Per-campaign STT/TTS provider, with an explicit fallback
--
-- `agent_config.stt_provider` and `tts_provider` have existed since the schema
-- was written and the agent has never read them: _stt_stack() and _tts_stack()
-- hardcode Sarvam with an OpenAI fallback. The columns have been decoration.
-- This migration is the schema half of making them real.
--
-- The fallback is a COLUMN, not a rule. It could have been inferred - "use
-- whichever other provider this client has a key for" - and that was rejected:
-- adding a key for one campaign would silently rewire another campaign's
-- fallback, and the first anyone would know is a call answered in a different
-- voice. NULL means no fallback: if the primary is down, the call is down.
--
-- Backfilled to exactly today's behaviour (sarvam -> openai, both layers), so
-- this migration changes no call's routing. The behaviour only changes when
-- somebody picks something different in the console.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

-- Migration 008's comment claimed a separate provider_keys table meant "a new
-- provider needs no migration". That was wrong in the same file that made it
-- wrong: the CHECK below pins the list. Adding a provider always needs code
-- anyway - a plugin import, a constructor, a key validator - so the constraint
-- is not the obstacle. It is a guard that must move in the same change.
ALTER TABLE provider_keys DROP CONSTRAINT IF EXISTS provider_keys_provider_chk;
ALTER TABLE provider_keys ADD CONSTRAINT provider_keys_provider_chk
    CHECK (provider IN ('openai', 'sarvam', 'soniox'));

ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS stt_fallback_provider TEXT,
    ADD COLUMN IF NOT EXISTS tts_fallback_provider TEXT;

-- Same list as provider_keys, plus NULL for "no fallback".
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'agent_config_stt_provider_chk') THEN
        ALTER TABLE agent_config ADD CONSTRAINT agent_config_stt_provider_chk
            CHECK (stt_provider IN ('openai', 'sarvam', 'soniox'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'agent_config_tts_provider_chk') THEN
        ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_provider_chk
            CHECK (tts_provider IN ('openai', 'sarvam', 'soniox'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'agent_config_stt_fb_chk') THEN
        ALTER TABLE agent_config ADD CONSTRAINT agent_config_stt_fb_chk
            CHECK (stt_fallback_provider IS NULL
                   OR (stt_fallback_provider IN ('openai', 'sarvam', 'soniox')
                       AND stt_fallback_provider <> stt_provider));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'agent_config_tts_fb_chk') THEN
        ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_fb_chk
            CHECK (tts_fallback_provider IS NULL
                   OR (tts_fallback_provider IN ('openai', 'sarvam', 'soniox')
                       AND tts_fallback_provider <> tts_provider));
    END IF;
END $$;

-- `<> stt_provider` above is not pedantry. A fallback equal to the primary is a
-- FallbackAdapter that retries the same dead provider twice and reports itself
-- as protected - which is worse than having no fallback, because the console
-- would show one.

-- Today's behaviour, written down. Nothing about routing changes here.
UPDATE agent_config
   SET stt_provider = COALESCE(stt_provider, 'sarvam'),
       tts_provider = COALESCE(tts_provider, 'sarvam'),
       stt_fallback_provider = COALESCE(stt_fallback_provider, 'openai'),
       tts_fallback_provider = COALESCE(tts_fallback_provider, 'openai');

COMMIT;
