-- =============================================================================
-- 003 — Backfill calls recorded without a tenant
--
-- Migration 001 backfilled the calls that existed at the time, but the WRITER
-- was never updated: store.start_call() kept inserting only config_name. Every
-- call recorded between 001 and now therefore has campaign_id = NULL, which
-- means a superadmin can see it (their scope is "all tenants") and the client it
-- actually belongs to never can.
--
-- agent/store.py now stamps both columns on insert. This repairs what was
-- already written.
--
-- Safe to re-run; touches only rows that are still NULL.
-- =============================================================================

BEGIN;

UPDATE calls c
   SET campaign_id = ac.campaign_id,
       tenant_id   = cam.tenant_id
  FROM agent_config ac
  JOIN campaigns cam ON cam.id = ac.campaign_id
 WHERE c.campaign_id IS NULL
   AND c.config_name = ac.name;

-- Anything still unlinked has a config_name that no longer matches any
-- agent_config row - worth seeing rather than silently leaving behind.
DO $$
DECLARE
    orphans BIGINT;
BEGIN
    SELECT count(*) INTO orphans FROM calls WHERE campaign_id IS NULL;
    IF orphans > 0 THEN
        RAISE WARNING 'calls still unlinked: % (config_name matches no agent_config)', orphans;
    END IF;
END $$;

COMMIT;
