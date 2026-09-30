-- Google Cloud Text-to-Speech, as a sixth voice provider.
--
-- texttospeech.googleapis.com - NOT the Gemini API that migration 063 added.
-- Two different products from one company, and this project now speaks to both:
--
--   'gemini'   generativelanguage.googleapis.com, a plain API key, our own
--              client, no streaming of the request - measured at ~2.0 s to the
--              first audio even after moving to streamGenerateContent
--   'google'   texttospeech.googleapis.com, a SERVICE ACCOUNT, livekit's own
--              plugin, and real streaming synthesis
--
-- WHY 'google' AND NOT SOMETHING NEWER-SOUNDING. It is what the product is
-- called and what the plugin is called (livekit.plugins.google). The name has
-- history in this database and it is worth knowing: the original schema
-- defaulted all three layers to 'google' before Step 8, and migration 006
-- repaired those defaults after ten load-test calls died on a Gemini model that
-- was never wired. Every row it touched was moved to sarvam, so nothing is
-- left that this CHECK would now silently bless.
--
-- THE CREDENTIAL IS A DIFFERENT SHAPE, and this is the one real cost. The
-- plugin takes credentials_info (the parsed service-account JSON) or
-- credentials_file; there is no api_key parameter, which was read off the
-- installed plugin on 30 Sep 2026 rather than assumed:
--
--   TTS.__init__(..., credentials_info: NotGivenOr[dict] = NOT_GIVEN,
--                     credentials_file: NotGivenOr[str] = NOT_GIVEN, ...)
--
-- provider_keys does NOT need to change for that. It stores one encrypted
-- string, and a service-account JSON is a string; it is parsed where it is
-- used. What does change is the console - a 2 KB credential does not belong in
-- a one-line input - and admin-api, which has no livekit plugins and therefore
-- has to mint its own OAuth token to preview a voice.
--
-- WHAT IT BUYS, from the same reading of the plugin:
--
--   TTSCapabilities(streaming=use_streaming), default True, and a real
--   SynthesizeStream over streaming_synthesize - so unlike kokoro and gemini
--   this is not wrapped in a StreamAdapter and does not pay a fixed cost per
--   clause
--
--   sample_rate, speaking_rate, pitch, effects_profile_id, location and
--   custom_pronunciations - of which pitch and pronunciation are levers no
--   provider in this system has ever had
--
-- Not allowed as an STT provider. Google's speech-to-text is a different API
-- again, and offering a provider for a layer it cannot serve is a row the
-- database refuses on save, which lands on the person rather than the dropdown.

ALTER TABLE provider_keys DROP CONSTRAINT IF EXISTS provider_keys_provider_chk;
ALTER TABLE provider_keys ADD CONSTRAINT provider_keys_provider_chk
    CHECK (provider IN ('openai', 'sarvam', 'soniox', 'openrouter', 'gemini', 'google'));

ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_tts_provider_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_provider_chk
    CHECK (tts_provider IN ('openai', 'sarvam', 'soniox', 'kokoro', 'gemini', 'google'));

-- NULL is "no fallback", and a fallback equal to the primary is not a fallback.
ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_tts_fb_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_fb_chk
    CHECK (tts_fallback_provider IS NULL
           OR (tts_fallback_provider IN ('openai', 'sarvam', 'soniox', 'kokoro',
                                         'gemini', 'google')
               AND tts_fallback_provider <> tts_provider));

-- What the guards allow now, in one place - the same closing query 050, 059 and
-- 063 end with, so the next person adding a provider sees every constraint at
-- once rather than finding the third one through a 500 on save.
SELECT conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conname IN ('provider_keys_provider_chk',
                   'agent_config_stt_provider_chk',
                   'agent_config_tts_provider_chk',
                   'agent_config_tts_fb_chk')
 ORDER BY conname;
