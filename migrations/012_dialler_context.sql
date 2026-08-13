-- =============================================================================
-- 012 — What the dialler told us about the call
--
-- The dialler sends per-call context as IAX2 variables: who is calling, what
-- they bought, and its own lead / service-request identifiers. Asterisk now
-- forwards them as SIP headers and livekit-sip turns them into participant
-- attributes.
--
-- Stored as JSONB rather than columns. The set is the dialler's to change - they
-- added seven fields without telling anyone, and the next one should not need a
-- migration to be visible.
--
-- Its real job is correlation. `dialer.lead_id` is what joins a call in this
-- console to a lead in their CRM; without it, "which call was that?" is answered
-- by comparing timestamps.
--
-- Only conversational fields reach the model - name, product, call type. The
-- identifiers stay here, because a model given a lead id will eventually read it
-- out to the caller.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE calls ADD COLUMN IF NOT EXISTS dialer_context JSONB;

-- "Find the call for lead X" is the question this column exists to answer, and
-- it is asked with an exact value. A GIN index on the whole document keeps that
-- cheap without having to guess which keys get searched.
CREATE INDEX IF NOT EXISTS calls_dialer_context_idx
    ON calls USING GIN (dialer_context jsonb_path_ops)
 WHERE dialer_context IS NOT NULL;

COMMIT;
