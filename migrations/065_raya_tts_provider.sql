-- Raya (Bakbak), hub.getraya.app, as a seventh voice provider.
--
-- Indic-first - hi, mr, te, kn, bn, as, gu, ne, ml, ta, en-in, en-us - and the
-- reason it is here is price: it is claimed cheaper than Soniox, and on latency
-- it measured close enough to Google that the difference is not what decides.
--
-- MEASURED FROM .243 ON 6 OCT 2026, before a line of this was written, one
-- short Hindi sentence, fresh connection each time:
--
--     raya   8 kHz    ttfb 341 ms   first audio 342 ms
--     raya  24 kHz    ttfb 541 ms   first audio 571 ms
--     google Chirp3-HD (bench, same box)         304 ms
--
-- ttfb and first audio are the same number, so the audio starts with the
-- response rather than after it, and step_time summed to 0.358 s for 2.56 s of
-- speech. Their work is seven times faster than real time; the wait is the
-- distance to them.
--
-- A PLAIN API KEY, so provider_keys needs nothing new - unlike google, whose
-- credential is a service-account document. Added to provider_keys_provider_chk
-- because there is a real account behind it, unlike kokoro.
--
-- Not allowed as an STT provider here. They publish one, and it can be added
-- when it has been measured; offering a provider for a layer nothing has tested
-- is how a campaign saves a row that fails on a call.
--
-- THE VOICE IS A UUID. There is no sensible default to write down, so the agent
-- refuses to build without one and says which field to fill - the same shape as
-- KOKORO_URL refusing rather than guessing an address, and the same lesson the
-- google provider taught last week when an empty voice quietly routed calls to
-- another backend entirely.

ALTER TABLE provider_keys DROP CONSTRAINT IF EXISTS provider_keys_provider_chk;
ALTER TABLE provider_keys ADD CONSTRAINT provider_keys_provider_chk
    CHECK (provider IN ('openai', 'sarvam', 'soniox', 'openrouter', 'gemini',
                        'google', 'raya'));

ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_tts_provider_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_provider_chk
    CHECK (tts_provider IN ('openai', 'sarvam', 'soniox', 'kokoro', 'gemini',
                            'google', 'raya'));

-- NULL is "no fallback", and a fallback equal to the primary is not a fallback.
ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_tts_fb_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_fb_chk
    CHECK (tts_fallback_provider IS NULL
           OR (tts_fallback_provider IN ('openai', 'sarvam', 'soniox', 'kokoro',
                                         'gemini', 'google', 'raya')
               AND tts_fallback_provider <> tts_provider));

-- What the guards allow now, in one place - the same closing query 050, 059,
-- 063 and 064 end with, so the next person adding a provider sees every
-- constraint at once rather than finding the third one through a 500 on save.
SELECT conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conname IN ('provider_keys_provider_chk',
                   'agent_config_stt_provider_chk',
                   'agent_config_tts_provider_chk',
                   'agent_config_tts_fb_chk')
 ORDER BY conname;
