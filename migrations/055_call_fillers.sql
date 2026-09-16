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

    -- A beat of silence in front of the acknowledgement.
    --
    -- NOT a delay before deciding whether to speak. That was the first design
    -- and a live call disproved it: livekit creates the reply's speech handle
    -- the moment the turn ends, and the queue plays in the order handles were
    -- made, so anything scheduled 300 ms later is heard AFTER the answer. The
    -- acknowledgement is therefore queued immediately, on every turn, and this
    -- number is silence at the front of its audio.
    --
    -- It still earns its place: it stops the agent answering the instant a
    -- caller pauses, and leaves them room to carry on - the sound is
    -- interruptible, and during this window nothing has been said yet.
    --
    -- Floor of 150, because below that there is no beat at all. Ceiling of
    -- 2000: this plus the sound itself is the most the reply can ever be
    -- delayed, and the median reply takes 1555 ms to arrive anyway.
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
