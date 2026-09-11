-- Let 'openrouter' actually be stored.
--
-- 049 taught the agent to read llm_provider and the console to offer OpenRouter,
-- and saving a key still failed with:
--
--   CheckViolationError: new row for relation "provider_keys" violates check
--   constraint "provider_keys_provider_chk"
--
-- The provider list lives in THREE places, not two: schemas.py, provider_keys.py,
-- and a CHECK in the database. Migration 011 says so in as many words - "a guard
-- that must move in the same change" - and 049 moved the two in Python and left
-- the third behind. The key validated against OpenRouter, came back accepted,
-- and then the INSERT hit the wall.

ALTER TABLE provider_keys DROP CONSTRAINT IF EXISTS provider_keys_provider_chk;
ALTER TABLE provider_keys ADD CONSTRAINT provider_keys_provider_chk
    CHECK (provider IN ('openai', 'sarvam', 'soniox', 'openrouter'));

-- llm_provider has never had one. It was a column nobody read until 049, so
-- there was nothing to guard; now the agent obeys it and a typo in that column
-- is a campaign that declines every call for want of a key it never needed.
--
-- Only the two that speak a language model. Sarvam and Soniox do speech, and
-- allowing them here would let the console save a configuration the agent
-- cannot build.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'agent_config_llm_provider_chk') THEN
        ALTER TABLE agent_config ADD CONSTRAINT agent_config_llm_provider_chk
            CHECK (llm_provider IN ('openai', 'openrouter'));
    END IF;

    -- Same shape as agent_config_stt_fb_chk: NULL is "no fallback", and a
    -- fallback equal to the primary is not a fallback. The console filters it
    -- out of the dropdown; this is the guard for everything that is not the
    -- console.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'agent_config_llm_fb_chk') THEN
        ALTER TABLE agent_config ADD CONSTRAINT agent_config_llm_fb_chk
            CHECK (llm_fallback_provider IS NULL
                   OR (llm_fallback_provider IN ('openai', 'openrouter')
                       AND llm_fallback_provider <> llm_provider));
    END IF;
END $$;

-- What each guard now allows, so the next person adding a provider can see all
-- of them in one place rather than finding the third one through a 500.
SELECT conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conname IN ('provider_keys_provider_chk',
                   'agent_config_llm_provider_chk',
                   'agent_config_llm_fb_chk',
                   'agent_config_stt_provider_chk',
                   'agent_config_tts_provider_chk')
 ORDER BY conname;
