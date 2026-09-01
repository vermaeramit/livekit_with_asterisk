-- When a human is actually there to take the call.
--
-- The agent answers around the clock. The people it hands calls to do not, and
-- until now nothing knew the difference: a transfer at 2am dialled a desk
-- nobody was at, and the caller heard ringing and then nothing.
--
-- The hardcoded string in voice_agent.py already said "tell the caller to call
-- back during office hours" - so the idea was in the code before the hours
-- were, with no way to say when those hours are and no way to change the
-- sentence.
--
-- ENFORCED IN THE AGENT, NOT IN THE PROMPT
--
-- A rule in the instructions is a suggestion the model follows most of the
-- time, and "most of the time" is not a business hour. The check runs in code
-- before the transfer, exactly like the confirmation gate. The hours also go
-- INTO the prompt, so the model does not promise a handoff it is about to be
-- refused - but the prompt is the courtesy and the code is the rule.

ALTER TABLE agent_config
    -- Off = transfer whenever, which is what every campaign does today. The
    -- default matters: this migration must change no behaviour on the day it
    -- runs, and somebody turns it on deliberately afterwards.
    ADD COLUMN IF NOT EXISTS transfer_hours_enabled BOOLEAN NOT NULL DEFAULT false,

    -- {"mon": ["09:30", "18:30"], ..., "sun": null}
    --
    -- null or a missing day means closed. Times are local to
    -- agent_config.prompt_timezone - NOT the server's clock, which is a
    -- distinction that only shows up at midnight and only in production.
    --
    -- One range per day because that is what the form offers. A split shift
    -- needs a migration, and it should: pretending the shape supports
    -- something the console cannot create is how a feature gets believed in.
    ADD COLUMN IF NOT EXISTS transfer_hours JSONB,

    -- [{"date": "2026-10-20", "label": "Diwali"}]
    --
    -- Per campaign, which means Diwali is entered once per campaign. That was
    -- chosen knowingly; the console offers a copy button to take the sting out.
    ADD COLUMN IF NOT EXISTS transfer_holidays JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- Spoken when a handoff is asked for outside those hours. {next_open} is
    -- replaced with when the team is next available, in the campaign's own
    -- language. Empty falls back to a built-in sentence - a caller asking for a
    -- person should never be met with silence because a field was left blank.
    ADD COLUMN IF NOT EXISTS transfer_closed_message TEXT;

COMMENT ON COLUMN agent_config.transfer_hours IS
    'Day -> [open, close] in prompt_timezone. null or absent = closed that day.';

-- Who asked for a person and could not have one.
--
-- The interesting list this produces is not "transfers that failed" but
-- "callers who wanted a human at 9pm" - which is a callback list, and a
-- reason to think about the hours themselves.
ALTER TABLE calls
    ADD COLUMN IF NOT EXISTS transfer_refused TEXT;

COMMENT ON COLUMN calls.transfer_refused IS
    'NULL = not refused. Otherwise why: closed, holiday. The caller asked for '
    'a person and the agent could not hand them over.';

CREATE INDEX IF NOT EXISTS calls_transfer_refused_idx
    ON calls (campaign_id, started_at DESC)
 WHERE transfer_refused IS NOT NULL;

SELECT count(*) AS campaigns,
       count(*) FILTER (WHERE transfer_hours_enabled) AS with_hours
  FROM agent_config;
