-- =============================================================================
-- 007 — Fix the default TTS voice
--
-- Migration 006 set tts_voice DEFAULT 'anushka'. That name was written from
-- memory and is a bulbul:v2 speaker; bulbul:v3 rejects it outright:
--
--     ValueError: Speaker 'anushka' is not compatible with model 'bulbul:v3'
--
-- The plugin raises in TTS.__init__, so the job dies before the call is
-- answered - the same "born broken" failure as the Gemini model default, from
-- the same cause: a default chosen from memory rather than from what the system
-- was already running successfully. The working config had 'shubh' all along.
--
-- The compatible list below is the plugin's own, taken from the error it
-- printed - not from documentation and not from memory.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE agent_config ALTER COLUMN tts_voice SET DEFAULT 'shubh';

-- Only rows whose speaker bulbul:v3 will actually reject. A campaign
-- deliberately running bulbul:v2 with a v2 speaker is left alone.
UPDATE agent_config
   SET tts_voice  = 'shubh',
       updated_at = now()
 WHERE COALESCE(tts_model, 'bulbul:v3') = 'bulbul:v3'
   AND tts_voice IS NOT NULL
   AND tts_voice NOT IN (
        'shubh','ritu','rahul','pooja','simran','kavya','amit','ratan','rohan',
        'dev','ishita','shreya','manan','sumit','priya','aditya','kabir','neha',
        'varun','roopa','aayan','ashutosh','advait','amelia','sophia','suhani',
        'rupali','tanya','shruti','kavitha');

DO $$
DECLARE
    bad BIGINT;
BEGIN
    SELECT count(*) INTO bad FROM agent_config
     WHERE COALESCE(tts_model, 'bulbul:v3') = 'bulbul:v3'
       AND tts_voice IS NOT NULL
       AND tts_voice NOT IN (
            'shubh','ritu','rahul','pooja','simran','kavya','amit','ratan','rohan',
            'dev','ishita','shreya','manan','sumit','priya','aditya','kabir','neha',
            'varun','roopa','aayan','ashutosh','advait','amelia','sophia','suhani',
            'rupali','tanya','shruti','kavitha');
    IF bad > 0 THEN
        RAISE WARNING 'agent_config rows with a voice bulbul:v3 will reject: %', bad;
    END IF;
END $$;

COMMIT;
