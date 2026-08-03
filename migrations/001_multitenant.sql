-- =============================================================================
-- 001 — Multi-tenant foundation
--
-- Introduces tenants -> campaigns and moves per-agent settings under a campaign,
-- so one client can run sales / support / collection with different prompts,
-- knowledge bases and voices.
--
-- DELIBERATELY BACKWARD COMPATIBLE. The running agent still reads
-- agent_config.config_name; this migration ADDS campaign_id alongside it and
-- backfills. Nothing the agent touches is renamed or dropped, so this can be
-- applied to a live system. Migration 002 will switch the agent over and only
-- then remove config_name.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

-- ─────────────────────────── tenants ───────────────────────────

CREATE TABLE IF NOT EXISTS tenants (
    id          BIGSERIAL PRIMARY KEY,
    -- slug is what appears in URLs; sequential ids stay internal
    slug        TEXT        NOT NULL UNIQUE,
    name        TEXT        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'active',   -- active | suspended
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tenants_status_chk CHECK (status IN ('active', 'suspended'))
);

-- ─────────────────────────── users ───────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    -- NULL tenant_id means a superadmin: our own team, sees every tenant
    tenant_id     BIGINT      REFERENCES tenants(id) ON DELETE CASCADE,
    email         TEXT        NOT NULL,
    name          TEXT,
    password_hash TEXT        NOT NULL,
    role          TEXT        NOT NULL,
    active        BOOLEAN     NOT NULL DEFAULT true,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT users_role_chk CHECK (
        role IN ('superadmin', 'tenant_admin', 'agent', 'viewer')),
    -- a superadmin must not belong to a tenant, and everyone else must
    CONSTRAINT users_tenant_role_chk CHECK (
        (role = 'superadmin' AND tenant_id IS NULL) OR
        (role <> 'superadmin' AND tenant_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON users (lower(email));
CREATE INDEX IF NOT EXISTS users_tenant_idx ON users (tenant_id);

-- ─────────────────────────── campaigns ───────────────────────────

CREATE TABLE IF NOT EXISTS campaigns (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    slug        TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    description TEXT,
    enabled     BOOLEAN     NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, slug)
);

CREATE INDEX IF NOT EXISTS campaigns_tenant_idx ON campaigns (tenant_id);

-- ─────────────────────────── seed the existing install ───────────────────────────
-- Everything built so far lives under agent_config.name = 'default'. Fold that
-- into a real tenant + campaign so nothing is orphaned.

INSERT INTO tenants (slug, name)
VALUES ('default', 'Default Tenant')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO campaigns (tenant_id, slug, name, description)
SELECT t.id, 'default', 'Default Campaign',
       'Created by migration 001 from agent_config.name = ''default'''
  FROM tenants t WHERE t.slug = 'default'
ON CONFLICT (tenant_id, slug) DO NOTHING;

-- ─────────────────────────── link existing tables ───────────────────────────
-- Added as NULLABLE and backfilled. config_name stays until the agent is
-- switched over in 002 - renaming it here would break the running workers.

ALTER TABLE agent_config  ADD COLUMN IF NOT EXISTS campaign_id BIGINT REFERENCES campaigns(id) ON DELETE CASCADE;
ALTER TABLE kb_documents  ADD COLUMN IF NOT EXISTS campaign_id BIGINT REFERENCES campaigns(id) ON DELETE CASCADE;
ALTER TABLE kb_chunks     ADD COLUMN IF NOT EXISTS campaign_id BIGINT REFERENCES campaigns(id) ON DELETE CASCADE;
ALTER TABLE calls         ADD COLUMN IF NOT EXISTS campaign_id BIGINT REFERENCES campaigns(id) ON DELETE SET NULL;
-- tenant_id on calls is denormalised on purpose: every list and chart in the
-- panel filters by tenant, and joining through campaigns on every query is waste
ALTER TABLE calls         ADD COLUMN IF NOT EXISTS tenant_id   BIGINT REFERENCES tenants(id)   ON DELETE SET NULL;

DO $$
DECLARE
    v_campaign BIGINT;
    v_tenant   BIGINT;
BEGIN
    SELECT c.id, c.tenant_id INTO v_campaign, v_tenant
      FROM campaigns c JOIN tenants t ON t.id = c.tenant_id
     WHERE t.slug = 'default' AND c.slug = 'default';

    UPDATE agent_config SET campaign_id = v_campaign WHERE campaign_id IS NULL;
    UPDATE kb_documents SET campaign_id = v_campaign WHERE campaign_id IS NULL;
    UPDATE kb_chunks    SET campaign_id = v_campaign WHERE campaign_id IS NULL;
    UPDATE calls        SET campaign_id = v_campaign, tenant_id = v_tenant
     WHERE campaign_id IS NULL;
END $$;

CREATE INDEX IF NOT EXISTS agent_config_campaign_idx ON agent_config (campaign_id);
CREATE INDEX IF NOT EXISTS kb_documents_campaign_idx ON kb_documents (campaign_id);
CREATE INDEX IF NOT EXISTS kb_chunks_campaign_idx    ON kb_chunks (campaign_id);
CREATE INDEX IF NOT EXISTS calls_campaign_idx        ON calls (campaign_id);
CREATE INDEX IF NOT EXISTS calls_tenant_started_idx  ON calls (tenant_id, started_at DESC);

-- ─────────────────────────── audit ───────────────────────────
-- "Who changed the prompt, and when" is a real question on a client-facing
-- product - especially when call quality drops right after a change.

CREATE TABLE IF NOT EXISTS config_audit (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   BIGINT      REFERENCES tenants(id) ON DELETE CASCADE,
    campaign_id BIGINT      REFERENCES campaigns(id) ON DELETE CASCADE,
    user_id     BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    entity      TEXT        NOT NULL,          -- agent_config | kb_document | campaign | user
    entity_id   TEXT,
    action      TEXT        NOT NULL,          -- create | update | delete | enable | disable
    changes     JSONB,                         -- {field: {from, to}}
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS config_audit_tenant_idx  ON config_audit (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS config_audit_entity_idx  ON config_audit (entity, entity_id);

-- ─────────────────────────── sessions ───────────────────────────
-- Refresh tokens are stored hashed so a database leak cannot be replayed, and
-- so an admin can revoke a session without rotating the signing key.

CREATE TABLE IF NOT EXISTS user_sessions (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_hash   TEXT        NOT NULL UNIQUE,
    user_agent     TEXT,
    ip             INET,
    expires_at     TIMESTAMPTZ NOT NULL,
    revoked_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS user_sessions_user_idx ON user_sessions (user_id);
CREATE INDEX IF NOT EXISTS user_sessions_exp_idx  ON user_sessions (expires_at);

COMMIT;
