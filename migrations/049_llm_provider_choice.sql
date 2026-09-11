-- Let a campaign choose its language model provider, and its fallback.
--
-- llm_provider has existed since the beginning and defaults to 'openai' - see
-- 006. The agent has simply never read it: _llm_stack built openai.LLM
-- unconditionally, with a hardcoded Gemini behind it. STT and TTS grew provider
-- columns and fallbacks; the LLM never did.
--
-- The Gemini fallback also ran on PLATFORM credentials rather than the client's,
-- because it authenticates with a service account file and there is nowhere in
-- the console to put one. So one leg of every campaign's language model was
-- billed to us and could not be changed by anyone. That is what this replaces.

ALTER TABLE agent_config
    -- NULL = no fallback, exactly like stt_fallback_provider. The agent also
    -- refuses a fallback whose provider has no key on this campaign, so an
    -- unreachable second leg degrades to "primary only" rather than to an error
    -- on the first call that needs it.
    ADD COLUMN IF NOT EXISTS llm_fallback_provider TEXT,

    -- AND ITS MODEL, which is where this differs from STT and TTS.
    --
    -- Those two fall back to a provider default - saarika:v2.5, bulbul:v3 - and
    -- the default is obvious because the provider makes one model. A gateway
    -- like OpenRouter makes none: the model name IS the routing, and there is
    -- no sensible value to guess. So the fallback leg names its own.
    --
    -- It also solves a problem the provider column alone would not: a campaign
    -- on openai/gpt-4.1-mini falling back to OpenRouter cannot reuse that name,
    -- because on OpenRouter the same model is "openai/gpt-4.1-mini".
    ADD COLUMN IF NOT EXISTS llm_fallback_model TEXT;

COMMENT ON COLUMN agent_config.llm_fallback_provider IS
    'NULL = no fallback. A second language model used when the primary fails, '
    'on this campaign''s own key - unlike the hardcoded Gemini leg it replaces, '
    'which ran on platform credentials.';

COMMENT ON COLUMN agent_config.llm_fallback_model IS
    'Required when llm_fallback_provider is set. Unlike STT and TTS there is no '
    'provider default to fall back to: on a gateway the model name is the '
    'routing.';

-- Nothing changes today. Every campaign keeps the provider it already had and
-- gains no fallback until somebody chooses one.
--
-- Worth seeing rather than assuming: this is the first time llm_provider has
-- ever mattered, and a row with something unexpected in it would now reach the
-- agent instead of being ignored.
SELECT llm_provider, count(*) AS campaigns,
       count(*) FILTER (WHERE llm_fallback_provider IS NOT NULL) AS with_fallback
  FROM agent_config GROUP BY 1 ORDER BY 1;
