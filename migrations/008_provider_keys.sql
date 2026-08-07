-- =============================================================================
-- 008 — Per-client / per-campaign provider keys
--
-- Each client brings its own OpenAI and Sarvam keys, so one client running out
-- of credits cannot stop another client's calls, and each client's spend lands
-- on its own account. Keys are MANDATORY: a campaign with no usable key is not
-- allowed to be enabled, and a call that somehow reaches one is handed to a
-- human rather than served on someone else's account.
--
-- Values are stored ENCRYPTED (Fernet, see agent/crypto.py). Plaintext keys must
-- never touch this table - a database dump or a nightly backup would otherwise
-- hand over every client's credentials at once. `key_hint` holds the last four
-- characters only, which is all the console ever displays.
--
-- Resolution order at call time: campaign key -> client key -> refuse.
-- There is deliberately no platform fallback. Falling back would keep calls
-- alive on OUR account while the client's key was broken, silently, for as long
-- as nobody looked at the invoice.
--
-- NOTE: this migration creates the table but CANNOT backfill it. Encryption
-- happens in application code, so the one-time import of today's shared platform
-- keys is a separate script (scripts/import_platform_keys.py). Until that runs,
-- every campaign resolves to "no key".
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

-- A campaign-scoped key must belong to the same tenant as the row that points at
-- it. Without this a mis-scoped write would attach client A's key to client B's
-- campaign - a cross-tenant credential leak that no application check would
-- catch after the fact. The composite foreign key below makes it impossible.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'campaigns_id_tenant_uniq') THEN
        ALTER TABLE campaigns
            ADD CONSTRAINT campaigns_id_tenant_uniq UNIQUE (id, tenant_id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS provider_keys (
    id          BIGSERIAL   PRIMARY KEY,
    tenant_id   BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- NULL = the client's default, used by every campaign that has no override.
    campaign_id BIGINT,
    provider    TEXT        NOT NULL,

    -- Fernet token: base64 ASCII, self-describing, authenticated. Stored as TEXT
    -- rather than BYTEA so a psql session cannot accidentally splatter binary,
    -- and so it is obvious at a glance that this column is not readable.
    key_enc     TEXT        NOT NULL,
    -- Last 4 characters of the plaintext. Enough for a human to confirm which
    -- key is in place, useless to anyone who steals the row.
    key_hint    TEXT        NOT NULL,

    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  BIGINT      REFERENCES users(id) ON DELETE SET NULL,

    CONSTRAINT provider_keys_provider_chk CHECK (provider IN ('openai', 'sarvam')),
    -- A hint longer than this is someone storing more of the key than intended.
    CONSTRAINT provider_keys_hint_chk     CHECK (length(key_hint) BETWEEN 1 AND 8),
    -- Cheap guard against a plaintext key being written into key_enc by mistake:
    -- every Fernet token starts with the version byte 0x80, base64'd as 'gAAAAA'.
    CONSTRAINT provider_keys_enc_chk      CHECK (key_enc LIKE 'gAAAAA%'),

    CONSTRAINT provider_keys_campaign_fk FOREIGN KEY (campaign_id, tenant_id)
        REFERENCES campaigns (id, tenant_id) ON DELETE CASCADE
);

-- One key per provider per scope. Two rows for the same thing would make which
-- key is live depend on row order.
CREATE UNIQUE INDEX IF NOT EXISTS provider_keys_scope_unique
    ON provider_keys (tenant_id, COALESCE(campaign_id, 0), provider);

-- The agent resolves a campaign's keys on every call, so that lookup is the hot
-- path: both the campaign override and the tenant default in one index scan.
CREATE INDEX IF NOT EXISTS provider_keys_lookup_idx
    ON provider_keys (tenant_id, provider, campaign_id);

COMMIT;
