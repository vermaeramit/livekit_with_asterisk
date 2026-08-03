-- =============================================================================
-- 004 — Campaign-aware routing
--
-- Until now every worker served one config, chosen by the AGENT_CONFIG env var.
-- That made two controls in the admin panel cosmetic: disabling a campaign
-- stopped nothing, and a second campaign could not take a call at all.
--
-- Routing key is the DIALLED NUMBER. A client's sales, support and collection
-- lines each get their own DID, which is how the caller already tells them
-- apart. The agent reads it from sip.trunkPhoneNumber.
--
-- NOT in scope: dropping agent_config.config_name. Knowledge-base retrieval
-- keys on it (kb_chunks.config_name, kb_documents.config_name), so removing it
-- means migrating the KB as well. Separate change.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS campaign_routes (
    id          BIGSERIAL PRIMARY KEY,
    campaign_id BIGINT      NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    -- The number as dialled. UNIQUE across every tenant on purpose: two clients
    -- cannot claim the same DID, and a collision should fail loudly at
    -- configuration time rather than send one client's caller to another's agent.
    did         TEXT        NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT campaign_routes_did_chk CHECK (did ~ '^[0-9A-Za-z+*#._-]{1,64}$')
);

CREATE INDEX IF NOT EXISTS campaign_routes_campaign_idx
    ON campaign_routes (campaign_id);

-- Extension 700 is what the dialplan sends to LiveKit today, so map it to the
-- campaign currently serving those calls. Without this the first call after
-- deploying would find no route.
INSERT INTO campaign_routes (campaign_id, did, description)
SELECT c.id, '700', 'Seeded by migration 004 - the existing test extension'
  FROM campaigns c JOIN tenants t ON t.id = c.tenant_id
 WHERE t.slug = 'default' AND c.slug = 'default'
ON CONFLICT (did) DO NOTHING;

COMMIT;
