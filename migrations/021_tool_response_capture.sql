-- =============================================================================
-- 021 — Keep a tool's answer, when someone asks for it
--
-- The dealer lookup returns a code and a name. The agent reads the NAMES to the
-- caller, correctly - nobody recites "10015" down a phone - so the caller picks
-- by name and the code is never spoken. Extraction reads the transcript, so it
-- can only ever record the name, which is exactly what was happening.
--
-- The code exists in one place: the tool's HTTP response. Migration 013
-- deliberately did not store those, and the reason still stands - a client API
-- answers with customer records, and keeping them would put personal data in a
-- table nobody thinks of as holding any.
--
-- So this is opt-in PER TOOL rather than on for everything. A dealer list is
-- business data; the next tool might return a phone number and an address, and
-- that decision should be made about that endpoint, on purpose, rather than
-- inherited from this one.
--
-- Retention is real, not aspirational: responses older than
-- TOOL_RESPONSE_RETENTION_DAYS are nulled by the console's sweeper. The
-- invocation row itself is never deleted - what a tool did is the audit trail,
-- and only the body ages out.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE campaign_tools
    ADD COLUMN IF NOT EXISTS keep_response BOOLEAN NOT NULL DEFAULT false;

-- Truncated to the tool's own max_response_bytes, which is already the amount
-- the model was given. Storing more than the model saw would be storing data
-- for no reader.
ALTER TABLE tool_invocations
    ADD COLUMN IF NOT EXISTS response TEXT;

-- What the purge scans. Partial, because the overwhelming majority of rows have
-- no response at all and should never be looked at.
CREATE INDEX IF NOT EXISTS tool_invocations_response_age_idx
    ON tool_invocations (created_at)
 WHERE response IS NOT NULL;

COMMIT;
