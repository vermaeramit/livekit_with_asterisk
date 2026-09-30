-- Gemini's own text-to-speech, as a fifth voice provider.
--
-- NOT Google Cloud Text-to-Speech. Those are two different products: Cloud TTS
-- is texttospeech.googleapis.com with Chirp/Neural2 voices and a service
-- account, while this is the Gemini API with the prebuilt voice names and a
-- plain API key. Only the second one fits provider_keys, which stores a single
-- encrypted string - a service-account JSON is a different shape and would have
-- needed storage work before any of this. See agent/gemini_tts.py.
--
-- 'google' APPEARS IN THIS DATABASE'S HISTORY AND MEANT SOMETHING ELSE.
-- The original schema defaulted all three layers to 'google', which was the
-- plan before Step 8 and was never wired for TTS at all; migration 006 repaired
-- those defaults after ten load-test calls died on gemini-flash-latest. This
-- provider is called 'gemini', not 'google', so nothing here can be confused
-- with that - and a row left over from then would not satisfy the new CHECK
-- either way, because 006 already moved them all to sarvam.
--
-- IT HAS A KEY, unlike kokoro. So unlike migration 059 it IS added to
-- provider_keys_provider_chk, the console will offer it on the key page, and
-- required_for_campaign will refuse to enable a campaign that selects it
-- without one.
--
-- Not allowed as an STT provider: this is the speech half of the API only.
--
-- ONE THING THE CONSTRAINT CANNOT SAY. The model name carries "preview", and a
-- preview model can be withdrawn on a date nothing in this repo would warn
-- about - Soniox removed seven voices with tts-rt-v2 and a campaign left on one
-- would have gone silent mid-call. A campaign whose voice is gemini should name
-- a fallback. The console warns; a CHECK cannot say "should".

ALTER TABLE provider_keys DROP CONSTRAINT IF EXISTS provider_keys_provider_chk;
ALTER TABLE provider_keys ADD CONSTRAINT provider_keys_provider_chk
    CHECK (provider IN ('openai', 'sarvam', 'soniox', 'openrouter', 'gemini'));

ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_tts_provider_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_provider_chk
    CHECK (tts_provider IN ('openai', 'sarvam', 'soniox', 'kokoro', 'gemini'));

-- NULL is "no fallback", and a fallback equal to the primary is not a fallback.
--
-- Gemini earns its place here more than as a primary: the campaign on our own
-- box currently shows "speaks on our own server and has no fallback", and a
-- fallback does not need 100 ms - it needs to exist when the GPU box does not.
ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_tts_fb_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_fb_chk
    CHECK (tts_fallback_provider IS NULL
           OR (tts_fallback_provider IN ('openai', 'sarvam', 'soniox', 'kokoro', 'gemini')
               AND tts_fallback_provider <> tts_provider));

-- What the guards allow now, in one place - the same closing query 050 and 059
-- end with, so the next person adding a provider sees every constraint at once
-- rather than finding the third one through a 500 on save.
SELECT conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conname IN ('provider_keys_provider_chk',
                   'agent_config_stt_provider_chk',
                   'agent_config_tts_provider_chk',
                   'agent_config_tts_fb_chk')
 ORDER BY conname;
