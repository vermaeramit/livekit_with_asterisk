-- How fast the agent speaks.
--
-- The console has had a speed slider next to the voice preview since the
-- preview was written, with "Speed applies to this preview only - it is not
-- saved with the campaign" underneath it. So the panel invites somebody to
-- find a speed they like and then throws it away. This is the column that
-- keeps it.
--
-- NOT specific to one provider. Every text-to-speech here takes a speed:
-- Sarvam calls it `pace`, Soniox, OpenAI and Kokoro call it `speed`, and
-- ttspreview has passed one to all three since it was written. So a campaign
-- sets it once and it applies to whichever voice that campaign uses.
--
-- WHY IT IS WORTH A COLUMN AT ALL: on 26 Sep the voice on campaign 7 was
-- described as sounding thick and robotic on the phone. The trunk was checked
-- and is ulaw, which is the best a phone line offers, so the codec is not the
-- cause. Speed is one of the few dials that changes how natural a synthetic
-- voice sounds, and a little slower is usually better on 8 kHz.
--
-- 0.5 to 2.0 because that is what the providers accept; outside it they either
-- clamp silently or refuse, and neither is something to discover on a call.

ALTER TABLE agent_config ADD COLUMN IF NOT EXISTS tts_speed REAL NOT NULL DEFAULT 1.0;

ALTER TABLE agent_config DROP CONSTRAINT IF EXISTS agent_config_tts_speed_chk;
ALTER TABLE agent_config ADD CONSTRAINT agent_config_tts_speed_chk
    CHECK (tts_speed >= 0.5 AND tts_speed <= 2.0);

COMMENT ON COLUMN agent_config.tts_speed IS
    'How fast the voice speaks, 0.5 to 2.0, 1.0 = the provider default. '
    'Applies to whichever TTS the campaign uses - Sarvam takes it as pace, the '
    'others as speed.';
