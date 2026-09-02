-- What actually went wrong, in columns rather than in a sentence.
--
-- The alert during the load test said "25.7% of the last 35 calls ended in an
-- error". True, acknowledged, and useless: the cause was OpenAI's token-per-
-- minute limit, and finding that out meant reading the worker journal.
--
-- WHY THE OLD RECORD COULD NOT ANSWER IT
--
-- calls.outcome held one long line like
--
--   LLMError: type='llm_error' timestamp=1787734781.0728912 label='...'
--   error=APIConnectionError("all LLMs failed ([...]) after 4.42 seconds")
--
-- with the timestamp INSIDE the text, so no two identical failures ever group
-- together. `GROUP BY outcome` over a fortnight returned eleven rows with a
-- count of one each. There was nothing to alert on and nothing to count.
--
-- The other thing buried in there: Sarvam ran out of credits twice. "No
-- credits available" is not a percentage to watch, it is a thing to be told
-- about the first time it happens, and it went past unnoticed.

CREATE TABLE IF NOT EXISTS call_errors (
    id          BIGSERIAL PRIMARY KEY,
    call_id     BIGINT REFERENCES calls(id) ON DELETE CASCADE,
    tenant_id   BIGINT,
    campaign_id BIGINT,

    -- llm | stt | tts | other. Which leg of the pipeline gave up.
    source      TEXT NOT NULL,
    -- openai | google | sarvam | soniox | ... , parsed from the plugin label.
    -- NULL when the label does not name one, which is what a FallbackAdapter
    -- error looks like: it reports itself, not the provider that failed.
    provider    TEXT,
    -- HTTP status where there is one. 429 is a rate limit, 402 is out of
    -- credit, 401 is a bad key - three different phone calls to three
    -- different people, and all three used to read as "an error".
    code        INTEGER,
    message     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The alert asks "how many errors for this tenant in the last N minutes",
-- which is this index and no other.
CREATE INDEX IF NOT EXISTS call_errors_scope_idx
    ON call_errors (tenant_id, created_at DESC);

COMMENT ON TABLE call_errors IS
    'One row per provider failure. calls.outcome keeps the full text; this is '
    'the part that can be counted, grouped and alerted on.';

-- A rule that fires on the errors themselves rather than on what fraction of
-- finished calls carried one. During a rate limit the calls are still running:
-- a percentage of completed calls reports the problem after it is over.
-- The constraint from 005 is named _chk, not _check. Dropping the wrong name
-- would have succeeded silently (IF EXISTS), left the old one in place, and
-- rejected every provider_errors rule the console tried to create - with the
-- new constraint sitting right there looking correct.
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_kind_chk;
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_kind_check;
ALTER TABLE alert_rules
    ADD CONSTRAINT alert_rules_kind_chk CHECK (kind IN (
        'latency_p95', 'error_rate', 'stale_calls', 'no_calls',
        'transfer_rate', 'limit_hits', 'provider_errors'));

-- Seeded for every tenant, because the lesson of the load test was not that
-- the alerting was broken - it fired, and it was acknowledged. It was that
-- nothing told anyone WHAT had failed. A rule nobody remembers to create
-- protects nothing.
--
-- Five in fifteen minutes: below a handful is one flaky request and not worth
-- a message at night; five is a pattern. 'critical' because every cause this
-- catches - out of credit, rate limited, a rejected key - stops calls until a
-- person does something.
INSERT INTO alert_rules (tenant_id, kind, threshold, window_minutes, severity)
SELECT t.id, 'provider_errors', 5.0, 15, 'critical' FROM tenants t
ON CONFLICT DO NOTHING;

SELECT pg_get_constraintdef(oid) AS kinds_allowed_now
  FROM pg_constraint WHERE conname = 'alert_rules_kind_chk';

SELECT kind, count(*) AS rules FROM alert_rules GROUP BY 1 ORDER BY 1;
