-- A text-to-speech provider that is ours, on our own hardware.
--
-- Kokoro runs on the GPU box at 10.130.9.248 behind an OpenAI-compatible API.
-- Measured from a laptop on the same LAN on 23 Sep 2026: first audio ~200 ms,
-- RTF 0.026 - nine seconds of Hindi rendered in a quarter of a second - against
-- Soniox's 400-900 ms on a good stretch and 1-5 s with holes on a bad one.
-- See gpu-server/BENCHMARKS.md for the numbers and how they were taken.
--
-- IT HAS NO KEY, and that is the only thing here that is structurally new.
-- Every provider until now was somebody else's account: provider_keys held the
-- credential and required_for_campaign refused to enable a campaign without it.
-- There is no account to hold for a box we own, so 'kokoro' is deliberately NOT
-- added to provider_keys_provider_chk - a key row for it would be a fiction -
-- and the console's key page will not offer it. The agent and the console both
-- skip it when they check which keys a campaign needs.
--
-- Where it lives is NOT in this table. The URL comes from KOKORO_URL in the
-- environment of each server, with no default: REPLICA.md records two days of
-- production calls landing on the development box because an address was
-- hardcoded, and a box on the LAN is exactly the kind of address that differs
-- between environments.
--
-- Not allowed as an STT provider: it only speaks.

ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_tts_provider_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_provider_chk
    CHECK (tts_provider IN ('openai', 'sarvam', 'soniox', 'kokoro'));

-- The fallback guard keeps the shape the others have: NULL is "no fallback",
-- and a fallback equal to the primary is not a fallback.
--
-- Kokoro is allowed here too, and it is worth saying why the reverse matters
-- more: a self-hosted provider is the first one in this system whose outage is
-- OURS to notice at 2 a.m., and this box has no monitoring. A campaign whose
-- voice is kokoro should always name a fallback. The console warns; this
-- constraint cannot, because "should" is not a thing a CHECK can say.
ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_tts_fb_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_fb_chk
    CHECK (tts_fallback_provider IS NULL
           OR (tts_fallback_provider IN ('openai', 'sarvam', 'soniox', 'kokoro')
               AND tts_fallback_provider <> tts_provider));

-- What the guards allow now, in one place - the same closing query migration
-- 050 ends with, for the same reason: the next person adding a provider should
-- see every constraint at once rather than finding the third one through a 500.
SELECT conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conname IN ('provider_keys_provider_chk',
                   'agent_config_stt_provider_chk',
                   'agent_config_tts_provider_chk',
                   'agent_config_tts_fb_chk')
 ORDER BY conname;
