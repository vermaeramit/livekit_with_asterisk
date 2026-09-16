-- Say something small while the caller waits.
--
-- Measured over 2,350 agent turns before building any of this:
--
--   llm_ttft   931 ms
--   tts_ttfb   653 ms
--   together  1555 ms
--
-- That is a second and a half of silence after every single thing a caller
-- says, and silence is what makes somebody say "hello?". An acknowledgement -
-- "जी…", "हम्म" - covers it the way a person does.
--
-- It cannot cover the whole gap between turns: turn detection is the larger
-- piece and comes BEFORE this, while the caller has only just stopped speaking.
-- Saying anything there would be interrupting them. The window this fills is
-- exactly llm_ttft + tts_ttfb.

ALTER TABLE agent_config
    -- Off everywhere. A campaign starts behaving exactly as it does today.
    ADD COLUMN IF NOT EXISTS reply_filler_enabled BOOLEAN NOT NULL DEFAULT false,

    -- ["जी…", "हम्म"] - one is picked at random per turn.
    --
    -- Both are deliberately CONTENT-FREE. The line has to be chosen before the
    -- model has read the caller's turn, so anything that agrees ("बिल्कुल"),
    -- understands ("समझ गई") or praises ("अच्छा सवाल") can land on a question
    -- and be nonsense - or be contradicted by the answer that follows it.
    ADD COLUMN IF NOT EXISTS reply_filler_lines JSONB,

    -- How long the reply may take before the acknowledgement is spoken.
    --
    -- 300 ms, where the tool filler waits 600. It can afford to be twice as
    -- eager because the audio is pre-rendered and plays instantly; the tool
    -- filler has to be synthesised while the caller waits, so it can only start
    -- when the wait is already long enough to be worth another 650 ms of TTS.
    --
    -- Floor of 150: at 0 it fires on turns that were never slow, and an
    -- acknowledgement before a fast answer is just a word in the way. Ceiling of
    -- 2000: the median gap is 1555 ms, so beyond that it would never speak.
    ADD COLUMN IF NOT EXISTS reply_filler_after_ms INTEGER NOT NULL DEFAULT 300,

    -- The same decision for the slow lookups: knowledge-base searches and tool
    -- calls. Was TOOL_FILLER_AFTER_MS, an environment variable on the box, and
    -- therefore the same number for every campaign on it - while what it should
    -- follow is how slow that campaign's own tools are.
    ADD COLUMN IF NOT EXISTS lookup_filler_after_ms INTEGER NOT NULL DEFAULT 600;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'agent_config_reply_filler_after_chk') THEN
        ALTER TABLE agent_config ADD CONSTRAINT agent_config_reply_filler_after_chk
            CHECK (reply_filler_after_ms BETWEEN 150 AND 2000);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'agent_config_lookup_filler_after_chk') THEN
        ALTER TABLE agent_config ADD CONSTRAINT agent_config_lookup_filler_after_chk
            CHECK (lookup_filler_after_ms BETWEEN 150 AND 5000);
    END IF;
END $$;

COMMENT ON COLUMN agent_config.reply_filler_lines IS
    'Short content-free acknowledgements, one picked per turn. Pre-rendered to '
    'the greeting cache on the call that first misses it, so it plays instantly '
    'afterwards. Never added to the chat context: the model must not see itself '
    'having spoken, and the transcript must not gain a turn carrying no answer.';

COMMENT ON COLUMN agent_config.lookup_filler_after_ms IS
    'How long a knowledge-base search or tool call may run before the caller is '
    'told something. Replaces the TOOL_FILLER_AFTER_MS environment variable, '
    'which set one number for every campaign on the box.';

-- Nothing changes today: the acknowledgement is off everywhere, and the lookup
-- wait keeps the 600 ms the environment variable already used.
SELECT count(*) AS campaigns,
       count(*) FILTER (WHERE reply_filler_enabled) AS with_acknowledgement,
       count(*) FILTER (WHERE kb_filler_enabled)    AS with_search_filler
  FROM agent_config;
