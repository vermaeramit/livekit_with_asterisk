-- Put campaign 1 into a state where a load test measures capacity.
--
--   docker exec -i postgres psql -U aivoice -d aivoice < loadtest-prepare.sql
--   ./loadtest.sh 5 0.7 700
--   docker exec -i postgres psql -U aivoice -d aivoice < loadtest-restore.sql
--
-- ALWAYS run the restore afterwards. It puts back exactly what was on, read
-- from a snapshot taken here rather than from anybody's memory of it.
--
--
-- WHY EACH OF THESE IS TURNED OFF
--
-- transfer   The synthetic caller plays `demo-thanks`, which has nothing to do
--            with motorcycles. The agent cannot answer it, and with transfer on
--            it hands the call to a human inside the first turn - so the run
--            measures twenty calls that lasted four seconds instead of twenty
--            calls under sustained load. loadtest.sh warns about this in its own
--            comments; it is the reason it defaults to 709.
--
-- tools      Twenty concurrent calls would hit apis.worxpertise.com at once.
--            That is the client's server, and if it slows down our numbers stop
--            measuring us and start measuring their rate limit.
--
-- postback   Every synthetic call would queue a delivery and retry it. The
--            endpoint is currently answering 404, so it is twenty more rows
--            failing on a schedule for no reason.
--
-- The knowledge base, prompt, models and endpointing are left exactly as they
-- are. They are the load: 108k tokens behind an index means a search on nearly
-- every turn, and each search is an embedding call on the same OpenAI account
-- the LLM uses. Testing without them would measure a system we do not run.

BEGIN;

-- Taken before anything changes, so the restore is a record rather than a guess.
--
-- DO NOTHING, not DO UPDATE. This script used to overwrite the snapshot every
-- time it ran, which meant a second run - after the first had already turned
-- everything off - recorded the OFF state as the thing to restore. Both
-- scripts then reported success and the campaign stayed disabled: transfer,
-- five tools and a postback that had delivered 113 times.
--
-- With DO NOTHING the first snapshot survives, and the restore deletes it when
-- it is applied, so the next prepare takes a fresh one.
INSERT INTO platform_settings (key, value)
SELECT 'loadtest_snapshot', jsonb_build_object(
           'taken_at',   now(),
           'campaign_id', 1,
           'transfer_enabled', (SELECT transfer_enabled FROM agent_config WHERE campaign_id = 1),
           'postback_enabled', (SELECT postback_enabled FROM agent_config WHERE campaign_id = 1),
           'enabled_tool_ids', coalesce(
               (SELECT jsonb_agg(id ORDER BY id) FROM campaign_tools
                 WHERE campaign_id = 1 AND enabled), '[]'::jsonb)
       )::text
ON CONFLICT (key) DO NOTHING;

UPDATE agent_config
   SET transfer_enabled = false,
       postback_enabled = false
 WHERE campaign_id = 1;

UPDATE campaign_tools SET enabled = false WHERE campaign_id = 1;

COMMIT;

-- Said loudly, because it means the previous run was never restored and the
-- values in the snapshot are older than they look.
SELECT CASE
         WHEN (value::jsonb ->> 'taken_at')::timestamptz < now() - interval '2 minutes'
         THEN '!! SNAPSHOT IS FROM AN EARLIER RUN - restore was never applied. '
              'It still holds the state from before THAT run, which is the one '
              'to put back.'
         ELSE 'snapshot taken'
       END AS step,
       value
  FROM platform_settings WHERE key = 'loadtest_snapshot';

SELECT transfer_enabled, postback_enabled,
       (SELECT count(*) FROM campaign_tools WHERE campaign_id = 1 AND enabled) AS tools_on
  FROM agent_config WHERE campaign_id = 1;
