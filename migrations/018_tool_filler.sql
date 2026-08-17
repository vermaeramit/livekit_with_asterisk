-- =============================================================================
-- 018 — Something to say while a tool is running
--
-- A tool call happens while someone is listening. The timeout is 2500 ms by
-- default, and every millisecond of it is silence on the line - the caller has
-- asked a question and nothing at all is happening.
--
-- Per tool, not per campaign: one campaign can have a dealer lookup that
-- answers in 200 ms and a booking call that takes two seconds, and they do not
-- want the same treatment.
--
-- NULL = say nothing, which is what every tool does today.
--
-- Note what is NOT stored here: when to say it. The filler is spoken only if
-- the tool has not answered within a fixed 600 ms, because a filler in front of
-- a fast API makes a short pause into a long one - "please wait a moment" takes
-- longer to say than the request took to run. 600 ms is below the point where
-- silence is noticed, so a fast tool stays silent and a slow one gets covered.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE campaign_tools
    ADD COLUMN IF NOT EXISTS filler_message TEXT;

ALTER TABLE campaign_tools
    DROP CONSTRAINT IF EXISTS campaign_tools_filler_chk;
ALTER TABLE campaign_tools
    ADD CONSTRAINT campaign_tools_filler_chk
    CHECK (filler_message IS NULL
           OR length(btrim(filler_message)) BETWEEN 1 AND 200);

COMMIT;
