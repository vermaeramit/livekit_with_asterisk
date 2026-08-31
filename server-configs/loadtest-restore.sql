-- Put campaign 1 back exactly as loadtest-prepare.sql found it.
--
--   docker exec -i postgres psql -U aivoice -d aivoice < loadtest-restore.sql
--
-- Reads the snapshot rather than assuming what was on. Assuming is how a
-- campaign comes out of a load test with its transfer still disabled and nobody
-- notices until a caller asks for a person and does not get one.
--
-- Refuses rather than guesses if there is no snapshot: leaving the settings as
-- they are and saying so is safer than turning on whatever seems likely.

DO $$
DECLARE
    snap jsonb;
BEGIN
    SELECT value::jsonb INTO snap
      FROM platform_settings WHERE key = 'loadtest_snapshot';

    IF snap IS NULL THEN
        RAISE EXCEPTION
            'no loadtest snapshot found - nothing was restored. Set transfer, '
            'postback and the tools by hand, or re-run prepare and restore in '
            'order.';
    END IF;

    UPDATE agent_config
       SET transfer_enabled = (snap->>'transfer_enabled')::boolean,
           postback_enabled = (snap->>'postback_enabled')::boolean
     WHERE campaign_id = (snap->>'campaign_id')::bigint;

    -- Only the tools that were on. A blanket `enabled = true` would switch on
    -- any tool that had been deliberately left off.
    UPDATE campaign_tools SET enabled = true
     WHERE campaign_id = (snap->>'campaign_id')::bigint
       AND id IN (SELECT (jsonb_array_elements_text(snap->'enabled_tool_ids'))::bigint);

    DELETE FROM platform_settings WHERE key = 'loadtest_snapshot';
END $$;

SELECT transfer_enabled, postback_enabled,
       (SELECT count(*) FROM campaign_tools WHERE campaign_id = 1 AND enabled) AS tools_on
  FROM agent_config WHERE campaign_id = 1;

SELECT id, name, enabled FROM campaign_tools WHERE campaign_id = 1 ORDER BY id;
