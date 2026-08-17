-- =============================================================================
-- 016 — Silence handling, transfer confirmation, and ending a call on purpose
--
-- Three gaps that all end the same way: a call that should have finished sits
-- open, or ends in a way nobody chose.
--
--   * A caller who says nothing is waited on until max_duration_sec. On a
--     dialler that hands over already-connected calls, silence usually means
--     the audio path is broken - and we hold the line for minutes.
--   * The agent cannot hang up. Only the guardrails end calls, and they say
--     "your time is up", which is not what a finished conversation sounds like.
--   * A transfer happens the moment the model decides on one. A caller who
--     says "no, wait" is already gone.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

-- NULL = the whole feature is off for this campaign. Deliberately nullable
-- rather than 0: "no timeout" and "a timeout of zero seconds" must not be the
-- same value, and a default that silently starts hanging up on existing
-- campaigns would be a surprise nobody asked for.
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS silence_timeout_sec INTEGER;

-- One line per attempt, in order. The LENGTH of this array is the number of
-- attempts - there is no separate count column, because two fields that must
-- agree eventually do not. The last line is spoken and then the call ends, so
-- it is the one to write as a goodbye.
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS silence_prompts TEXT[];

-- Ask before handing over. The confirmation is enforced in the agent by state,
-- not by trusting the model to pass a flag - see transfer_to_human.
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS transfer_confirm BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS transfer_confirm_message TEXT;

-- The marker the model writes when the conversation is over. Filtered out of
-- the spoken text and never reaches TTS. Configurable so a campaign whose
-- prompt already uses square brackets for something else can move it.
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS end_call_marker TEXT NOT NULL DEFAULT '[EOC]';

-- A timeout under 3s fires while the caller is drawing breath; over 60s the
-- call is already lost. Both ends are product decisions, not technical limits.
ALTER TABLE agent_config
    DROP CONSTRAINT IF EXISTS agent_config_silence_timeout_chk;
ALTER TABLE agent_config
    ADD CONSTRAINT agent_config_silence_timeout_chk
    CHECK (silence_timeout_sec IS NULL
           OR silence_timeout_sec BETWEEN 3 AND 60);

-- An empty array would enable the timeout with nothing to say, which reads as
-- "hang up silently after N seconds" - never what anyone means.
ALTER TABLE agent_config
    DROP CONSTRAINT IF EXISTS agent_config_silence_prompts_chk;
ALTER TABLE agent_config
    ADD CONSTRAINT agent_config_silence_prompts_chk
    CHECK (silence_prompts IS NULL
           OR (array_length(silence_prompts, 1) BETWEEN 1 AND 5));

COMMIT;
