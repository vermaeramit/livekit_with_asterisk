-- A language model on our own hardware.
--
-- Qwen3-32B-AWQ served by vLLM on the GPU box. Measured from .243 against the
-- `default` campaign's real prompt - 26,000 characters, a knowledge base index
-- and six tool declarations - beside gpt-4.1-mini on exactly the same prompt:
--
--     qwen3-32b      cold  456 ms   warm p50 106 ms   spread   6 ms
--     gpt-4.1-mini   cold 3298 ms   warm p50 700 ms   spread 322 ms
--
-- The spread is the number that decides it. gpt-4.1-mini was chosen for
-- variance in the first place - it cut the spread from 800 ms to 85 ms after a
-- single 6286 ms turn ended a call. This is 6 ms.
--
-- Of gpt-4.1-mini's 700 ms, about 450 was distance: measured on 23 Sep from
-- OpenAI's own openai-processing-ms header. The box is on the LAN, 5 ms away.
--
-- NAMED qwen-llm, not qwen, which migration 060 already gave to Qwen3-ASR on
-- the same box. Two services, two ports, and costing looks a rate up by this
-- name - one string for both would price speech as though it were text.
--
-- KEYLESS, like kokoro and qwen: no account, no provider_keys row, and the
-- console's key page does not offer it. Its address is QWEN_LLM_URL in each
-- server's environment, with no default.
--
-- WHAT THIS MIGRATION DOES NOT SETTLE: whether the model is any good. Latency
-- was measured; tool calls, grounding and Hindi were not. A campaign should be
-- tried in the console's chat tester - which runs the campaign's real prompt
-- and real tools and shows each tool call - before it is pointed at a caller.

ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_llm_provider_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_llm_provider_chk
    CHECK (llm_provider IN ('openai', 'openrouter', 'qwen-llm'));

ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_llm_fb_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_llm_fb_chk
    CHECK (llm_fallback_provider IS NULL
           OR (llm_fallback_provider IN ('openai', 'openrouter', 'qwen-llm')
               AND llm_fallback_provider <> llm_provider));

SELECT conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conname IN ('agent_config_llm_provider_chk',
                   'agent_config_llm_fb_chk',
                   'agent_config_stt_provider_chk',
                   'agent_config_tts_provider_chk')
 ORDER BY conname;
