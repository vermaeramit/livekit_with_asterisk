-- =============================================================================
-- 022 — Acknowledgements for things the server cannot check itself
--
-- The console warns that a database dump alone cannot restore this system:
-- SECRETS_KEY lives in .env, not in the database, and without it a restore
-- yields provider credentials nobody can decrypt.
--
-- Nothing here can verify that somebody has stored that key somewhere safe. So
-- the warning could never be satisfied, and a warning that can never be
-- satisfied is one people learn to scroll past - taking the real ones with it.
--
-- This records the human answer instead: who said it was done, and when.
--
-- The fingerprint is what makes it honest. It is a truncated SHA-256 of the key
-- - never the key, and not reversible - so if SECRETS_KEY is ever rotated, the
-- stored fingerprint stops matching and the warning comes back on its own. An
-- acknowledgement that outlives the thing it was about is worse than none.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS system_acks (
    key         TEXT        PRIMARY KEY,
    -- Identifies WHAT was acknowledged, without storing it. NULL for
    -- acknowledgements that have nothing to fingerprint.
    fingerprint TEXT,
    acked_by    BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    -- Kept as text too: the user row can be deleted, and "who confirmed this"
    -- is the part that has to survive them leaving.
    acked_name  TEXT,
    acked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    note        TEXT
);

COMMIT;
