-- Speech recognition on our own hardware.
--
-- Qwen3-ASR served by vLLM on the GPU box, behind OpenAI's
-- /v1/audio/transcriptions. Measured from .243 on 25 Sep 2026 against 8 kHz
-- Hindi: a fixed ~185 ms plus ~20 ms per second of audio, so the tail chunk a
-- call sends costs around 200-230 ms. See gpu-server/BENCHMARKS.md.
--
-- Like kokoro (migration 059) it has NO KEY, and for the same reason: there is
-- no account behind a box we own. It is not added to provider_keys_provider_chk
-- and the console's key page will not offer it; KEYLESS in the agent and in the
-- console skip it when checking which keys a campaign needs.
--
-- Where it lives is QWEN_STT_URL in each server's environment, with no default -
-- REPLICA.md records two days of production calls landing on the development box
-- because an address was hardcoded.
--
-- ONE DIFFERENCE FROM EVERY OTHER RECOGNISER HERE, worth knowing before a
-- campaign is switched to it: it does not stream. Soniox returns interim
-- transcripts while the caller is still speaking and the turn detector reads
-- them; this returns one transcript per utterance, after the VAD decides the
-- caller stopped. livekit does that chunking itself. Whether it helps or hurts
-- the 1500 ms the turn detector currently spends on half of all turns is a
-- question only a real call answers.
--
-- Not allowed as a TTS provider: it only listens.

ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_stt_provider_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_stt_provider_chk
    CHECK (stt_provider IN ('openai', 'sarvam', 'soniox', 'qwen'));

ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_stt_fb_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_stt_fb_chk
    CHECK (stt_fallback_provider IS NULL
           OR (stt_fallback_provider IN ('openai', 'sarvam', 'soniox', 'qwen')
               AND stt_fallback_provider <> stt_provider));

SELECT conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conname IN ('provider_keys_provider_chk',
                   'agent_config_stt_provider_chk',
                   'agent_config_stt_fb_chk',
                   'agent_config_tts_provider_chk',
                   'agent_config_tts_fb_chk')
 ORDER BY conname;
