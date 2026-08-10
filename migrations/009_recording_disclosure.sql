-- =============================================================================
-- 009 — Recording disclosure
--
-- Every call on this platform is recorded, and until now no caller was told so.
-- The dialplan records unconditionally - `Dial(..., b(recsetup^s^1))` runs on
-- every call regardless of what the campaign says - so this is not a per-campaign
-- nicety, it is the notice that makes the recording lawful to keep.
--
-- Its own column rather than text inside `greeting`, because the greeting is the
-- most-edited field in the console. A disclosure living inside it would survive
-- exactly until somebody rewrote the greeting for an unrelated reason, and
-- nothing would fail when it disappeared.
--
-- NOT NULL with a non-empty CHECK for the same reason. The constraint exists
-- because recording is unconditional; the day recording becomes a per-campaign
-- choice, revisit this constraint in the same change - a campaign that records
-- nothing has nothing to disclose.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS recording_disclosure TEXT NOT NULL
        DEFAULT 'Yeh call quality aur training ke liye record ki ja rahi hai.';

-- Whitespace is not a disclosure. Without this the field can be "cleared" to a
-- single space and look set in every listing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'agent_config_disclosure_chk') THEN
        ALTER TABLE agent_config
            ADD CONSTRAINT agent_config_disclosure_chk
            CHECK (length(btrim(recording_disclosure)) > 0);
    END IF;
END $$;

COMMIT;
