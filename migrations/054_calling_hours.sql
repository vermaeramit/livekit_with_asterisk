-- When a campaign may be dialled at all.
--
-- The dialler asks how many calls it can place (migration 052). That answer was
-- true at three in the morning, and the dialler would have believed it.
--
-- DELIBERATELY the same shape as transfer_hours, from migration 035, so that
-- hours.py evaluates both with one implementation and the console renders both
-- with one component. A second, simpler "start and end time" would have been
-- more work, not less - and it would have had no notion of Sunday, so the first
-- weekend would have had the dialler calling people at home.

ALTER TABLE agent_config
    -- false = no restriction, which is what every campaign has today. The
    -- capacity endpoint answers the same as before until somebody turns this on.
    ADD COLUMN IF NOT EXISTS calling_hours_enabled BOOLEAN NOT NULL DEFAULT false,

    -- {"mon": ["09:30", "18:30"], ..., "sun": null}
    --
    -- null or a missing day means no calling that day. Times are local to
    -- agent_config.prompt_timezone, NOT the server clock - the container runs on
    -- UTC, and the difference is invisible until it is 23:00 IST and the window
    -- says 17:30.
    ADD COLUMN IF NOT EXISTS calling_hours JSONB,

    -- Its OWN list, not transfer_holidays.
    --
    -- Sharing that column was the obvious move and it is wrong: the name says
    -- transfer, and a holiday silently governing whether the dialler may call
    -- is exactly the hidden coupling this schema keeps warning about. The
    -- console offers a copy button, the same one transfer hours already has, so
    -- entering Diwali twice costs one click.
    ADD COLUMN IF NOT EXISTS calling_holidays JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN agent_config.calling_hours IS
    'Day -> [open, close] in prompt_timezone. null or absent = no calling that '
    'day. Read by the dialler capacity endpoint, which returns zeros outside '
    'the window.';

COMMENT ON COLUMN agent_config.calling_holidays IS
    'Dates the dialler may not call, [{"date": "2026-10-20", "label": "Diwali"}]. '
    'Separate from transfer_holidays on purpose - one list governing two '
    'different decisions would be a surprise to whoever edited it.';

-- ── What this does NOT do ───────────────────────────────────────────────────
-- It does not refuse calls. Outside the window the capacity endpoint reports
-- zero slots and the dialler stops; a call that arrives anyway is still
-- answered. Refusing one means a caller is on the line and has to hear
-- something, and choosing what they hear is a different decision from this one.
--
-- So this is advisory, and honest about it: if the dialler ignores the answer,
-- nothing here stops it.

-- Nothing changes today - no campaign has a window, so the endpoint behaves
-- exactly as it did.
SELECT count(*) AS campaigns,
       count(*) FILTER (WHERE calling_hours_enabled)  AS with_calling_hours,
       count(*) FILTER (WHERE transfer_hours_enabled) AS with_transfer_hours
  FROM agent_config;
