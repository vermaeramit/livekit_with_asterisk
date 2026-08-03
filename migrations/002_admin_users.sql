-- =============================================================================
-- 002 — Panel-managed users
--
-- Adds what is needed to hand accounts out from the admin panel rather than
-- seeding them by hand: a forced first-password change, and a record of who
-- created whom.
--
-- Additive only. Nothing the agent reads is touched.
-- Safe to re-run.
--
-- (The agent's switch from config_name to campaign_id moves to migration 003.)
-- =============================================================================

BEGIN;

-- An admin picks the initial password, so the user must replace it before the
-- account is usable. Without this the admin knows every client's password.
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;

-- Who created the account. ON DELETE SET NULL so removing an admin does not
-- take the accounts they created with them.
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by BIGINT REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

-- Campaign slugs end up in agent_config.name, which the workers key on. Keep
-- them mechanically safe rather than trusting the UI to validate.
ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS campaigns_slug_chk;
ALTER TABLE campaigns ADD CONSTRAINT campaigns_slug_chk
    CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$' OR slug ~ '^[a-z0-9]$');

ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_slug_chk;
ALTER TABLE tenants ADD CONSTRAINT tenants_slug_chk
    CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$' OR slug ~ '^[a-z0-9]$');

-- config_audit is written on every mutation from the panel; this is the index
-- the "what changed on this campaign" view will read.
CREATE INDEX IF NOT EXISTS config_audit_campaign_idx
    ON config_audit (campaign_id, created_at DESC);

COMMIT;
