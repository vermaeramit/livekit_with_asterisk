-- How many calls a campaign may run at once, and what the rest hear.
--
-- The point of the wait is that it costs nothing. A call held here has not
-- reached LiveKit: no room, no agent job, no STT stream, no LLM request. It is
-- Asterisk playing a file off disk. The AI bill starts when the caller is
-- connected to the agent and not a second earlier, which is the whole reason
-- the queue lives in the dialplan rather than in the agent.
--
-- Every column here is NULL or a default that changes nothing. A campaign with
-- max_parallel_calls unset behaves exactly as it does today - straight to
-- LiveKit, no check, no wait. This is opt-in, one campaign at a time.
--
-- Read by Asterisk over ODBC (func_odbc), joined from campaign_routes.did, so
-- these names are load-bearing outside the application. Renaming one means
-- editing the dialplan in the same change.

ALTER TABLE agent_config
    -- NULL = unlimited, which is what every campaign is now. Zero is not the
    -- way to say "unlimited": somebody typing 0 in the console means "stop
    -- taking calls", and the two must not collide.
    ADD COLUMN IF NOT EXISTS max_parallel_calls INTEGER,

    -- What the caller hears while waiting. Stored as TEXT and synthesised once
    -- when it is saved - never per call, which would put a TTS request in front
    -- of exactly the callers we are trying not to spend money on.
    ADD COLUMN IF NOT EXISTS queue_message TEXT,

    -- Where that synthesis landed: an absolute path with NO extension, because
    -- that is what Asterisk's Playback() wants - it picks the format itself.
    -- The basename is a hash of the text and the voice, so changing either
    -- produces a different file and the old one is simply never asked for.
    --
    -- NULL means nothing has been rendered yet. The dialplan checks this and
    -- skips straight to the fallback rather than playing silence at somebody.
    ADD COLUMN IF NOT EXISTS queue_audio_file TEXT,

    -- Silence between repeats. Not zero: back-to-back playback of the same
    -- sentence sounds like a stuck line, which is when people hang up.
    ADD COLUMN IF NOT EXISTS queue_gap_seconds INTEGER NOT NULL DEFAULT 8,

    -- How long anyone is asked to wait. This is the only brake on an outbound
    -- campaign - the dialler cannot see that the bot is full and will keep
    -- sending calls - so it is deliberately short by default.
    ADD COLUMN IF NOT EXISTS queue_max_wait_seconds INTEGER NOT NULL DEFAULT 90,

    -- What happens when that runs out. 'human' is the 800 extension the
    -- dialplan already falls back to when the AI is unreachable; reusing it
    -- means there is one answer to "the bot cannot take this call" instead of
    -- two that can drift apart.
    ADD COLUMN IF NOT EXISTS queue_timeout_action TEXT NOT NULL DEFAULT 'human',

    -- Said before hanging up, when the action is 'hangup'. Optional, but the
    -- alternative is ninety seconds of "please hold" followed by a dead line,
    -- which reads as a fault rather than a decision.
    ADD COLUMN IF NOT EXISTS queue_timeout_message TEXT,
    ADD COLUMN IF NOT EXISTS queue_timeout_audio_file TEXT;

-- Named _chk to match every other constraint in this schema. Getting this wrong
-- has already cost one debugging session: DROP CONSTRAINT on a name that does
-- not exist fails, but ADD CONSTRAINT with a new name leaves the old one in
-- place and enforcing, silently.
ALTER TABLE agent_config
    DROP CONSTRAINT IF EXISTS agent_config_queue_timeout_action_chk;
ALTER TABLE agent_config
    ADD CONSTRAINT agent_config_queue_timeout_action_chk
        CHECK (queue_timeout_action IN ('human', 'hangup'));

-- Guard rails on the numbers. These are typed into a console by a person, and
-- a stray zero in the wait becomes a caller held for three hours.
ALTER TABLE agent_config
    DROP CONSTRAINT IF EXISTS agent_config_queue_numbers_chk;
ALTER TABLE agent_config
    ADD CONSTRAINT agent_config_queue_numbers_chk
        CHECK (
            (max_parallel_calls IS NULL OR max_parallel_calls BETWEEN 0 AND 500)
            AND queue_gap_seconds      BETWEEN 1 AND 60
            AND queue_max_wait_seconds BETWEEN 5 AND 600
        );

COMMENT ON COLUMN agent_config.max_parallel_calls IS
    'Calls this campaign may have with the agent at once. NULL = unlimited, '
    '0 = accept none. Enforced in the Asterisk dialplan before the call ever '
    'reaches LiveKit, so waiting callers cost nothing in STT, TTS or LLM.';

COMMENT ON COLUMN agent_config.queue_audio_file IS
    'Absolute path with NO extension, for Asterisk Playback(). Basename is a '
    'hash of the message text and the voice it was rendered in.';

-- Nothing should have changed today. Every campaign unlimited, every queue
-- unrendered - which is the same behaviour as before this ran.
SELECT count(*)                                            AS campaigns,
       count(*) FILTER (WHERE max_parallel_calls IS NOT NULL) AS limited,
       count(*) FILTER (WHERE queue_audio_file  IS NOT NULL)  AS with_audio
  FROM agent_config;
