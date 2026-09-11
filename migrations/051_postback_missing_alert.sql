-- Notice a call whose result was never even queued.
--
-- 048 added postback_failures, and it counts rows in call_postbacks. Call 590
-- had no row: a completed 15-turn conversation with Send to API on, killed by a
-- shutdown deadline before the INSERT ran. No row, no error, nothing in the
-- console - and nothing for 048 to count, because that rule can only see calls
-- that at least got as far as being queued.
--
-- So the alert added yesterday to catch "the customer stopped receiving their
-- data" could not see the worst version of it. That gap closes here.
--
-- The cause is already fixed in the agent. This exists for the next cause: a
-- rule that only watches the failure you have already understood finds nothing.
--
-- The constraint is named _chk, NOT _check. Dropping the wrong name succeeds
-- silently with IF EXISTS, leaves the old constraint enforcing, and the ADD
-- below then fails on rows the old one still rejects. 048 says the same thing;
-- it has cost a debugging session here before.

ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_kind_chk;
ALTER TABLE alert_rules
    ADD CONSTRAINT alert_rules_kind_chk CHECK (kind IN (
        'latency_p95', 'error_rate', 'stale_calls', 'no_calls',
        'transfer_rate', 'limit_hits', 'provider_errors',
        'postback_failures', 'postback_missing'));

-- Seeded for every tenant, for the reason 039 and 048 seeded theirs: a rule
-- nobody remembers to create is a rule that does not exist, and this whole
-- class of failure is defined by nobody noticing it.
--
-- Threshold 2, where postback_failures uses 3. A failed delivery is retried and
-- may yet succeed, so one of them is not news. This has no retry at all - the
-- row was never written - so the second one is already a pattern worth saying
-- out loud.
--
-- 60 minutes, and the rule itself ignores anything that ended in the last five:
-- the row is written as the call ends, and a window with no grace period would
-- report every healthy call that had just hung up.
--
-- 'warning', not 'critical', and for the same reason as 048: the caller was
-- served and hung up happy. What broke is the record of it reaching the
-- customer. That is a message, not a phone call at night.
INSERT INTO alert_rules (tenant_id, kind, threshold, window_minutes, severity)
SELECT t.id, 'postback_missing', 2.0, 60, 'warning' FROM tenants t
ON CONFLICT DO NOTHING;

-- What exists now.
SELECT kind, count(*) AS rules FROM alert_rules GROUP BY 1 ORDER BY 1;

-- And the thing this was built to see: finished calls, on a campaign that is
-- meant to send, that never queued anything. Anything older than today is
-- history rather than an incident - call 590 is in here until it is rebuilt.
SELECT c.config_name, count(*) AS never_queued,
       min(c.ended_at) AS oldest, max(c.ended_at) AS newest
  FROM calls c
  JOIN agent_config ac ON ac.name = c.config_name
  LEFT JOIN call_postbacks p ON p.call_id = c.id
 WHERE ac.postback_enabled
   AND c.turn_count > 0
   AND c.ended_at IS NOT NULL
   AND p.call_id IS NULL
 GROUP BY 1 ORDER BY 2 DESC;
