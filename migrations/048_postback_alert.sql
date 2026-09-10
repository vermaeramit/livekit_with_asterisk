-- Tell somebody when a call's result does not reach the customer.
--
-- Nothing watched this. 139 postbacks accumulated over several weeks, every one
-- a 404, still failing on the day it was found - and it was found by building a
-- System page, not by anything alerting. Every other kind of failure in this
-- system has a rule; the one where the customer silently stops receiving their
-- own data did not.
--
-- The constraint is named _chk, NOT _check. Dropping the wrong name succeeds
-- silently with IF EXISTS, leaves the old constraint in place and enforcing, and
-- the ADD below then fails on rows the old one still rejects. That has cost a
-- debugging session here before.

ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_kind_chk;
ALTER TABLE alert_rules
    ADD CONSTRAINT alert_rules_kind_chk CHECK (kind IN (
        'latency_p95', 'error_rate', 'stale_calls', 'no_calls',
        'transfer_rate', 'limit_hits', 'provider_errors',
        'postback_failures'));

-- Seeded for every tenant rather than left to be created, for the same reason
-- 039 seeded provider_errors: a rule nobody remembers to make is a rule that
-- does not exist, and this is the failure that ran for weeks unseen.
--
-- Threshold 3 in 60 minutes. Not 1: a single failure is a retry that has not
-- finished yet, and an alert on every one of those teaches people to ignore
-- the alert. Three in an hour is a pattern.
--
-- 'warning', not 'critical'. Nobody's call is broken - the caller was served
-- and hung up happy. What is broken is the record of it reaching the customer,
-- and that is worth a message rather than a phone call at night.
INSERT INTO alert_rules (tenant_id, kind, threshold, window_minutes, severity)
SELECT t.id, 'postback_failures', 3.0, 60, 'warning' FROM tenants t
ON CONFLICT DO NOTHING;

-- What exists now, and how many rules each tenant carries.
SELECT kind, count(*) AS rules FROM alert_rules GROUP BY 1 ORDER BY 1;

-- And the thing that prompted it: what is sitting undelivered right now.
SELECT status, count(*),
       coalesce(max(last_status_code)::text, 'no response') AS code
  FROM call_postbacks
 WHERE status <> 'sent'
 GROUP BY 1 ORDER BY 2 DESC;
