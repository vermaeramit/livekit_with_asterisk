-- =============================================================================
-- 005 — Alerting
--
-- Rules are evaluated by the admin API on a timer, and every firing is written
-- to `alerts` before any webhook is attempted. The record is the source of
-- truth; delivery is best effort. A webhook that fails must not lose the alert.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

-- A client's own channel. Slack, Teams and most chat tools accept a plain JSON
-- POST, so no per-provider integration is needed.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS webhook_url TEXT;

CREATE TABLE IF NOT EXISTS alert_rules (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- NULL = every campaign in the tenant
    campaign_id    BIGINT      REFERENCES campaigns(id) ON DELETE CASCADE,
    kind           TEXT        NOT NULL,
    threshold      REAL        NOT NULL,
    window_minutes INTEGER     NOT NULL DEFAULT 15,
    -- Below this many calls in the window the sample is too small to judge, and
    -- a percentage rule would fire on one bad call out of two.
    min_calls      INTEGER     NOT NULL DEFAULT 5,
    severity       TEXT        NOT NULL DEFAULT 'warning',
    enabled        BOOLEAN     NOT NULL DEFAULT true,

    -- Edge-triggered. A rule fires when the condition becomes true and stays
    -- quiet while it remains true, so a bad afternoon produces one alert rather
    -- than one every evaluation. `firing` clears when the condition clears,
    -- which re-arms it.
    firing         BOOLEAN     NOT NULL DEFAULT false,
    last_fired_at  TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ,

    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT alert_rules_kind_chk CHECK (kind IN (
        'latency_p95',    -- p95 turn latency, ms
        'error_rate',     -- share of calls ending in error, %
        'transfer_rate',  -- share handed to a human, %
        'limit_hits',     -- share stopped by a guardrail, %
        'no_calls',       -- zero calls in the window
        'stale_calls'     -- calls open past their duration limit
    )),
    CONSTRAINT alert_rules_severity_chk CHECK (severity IN ('warning', 'critical')),
    CONSTRAINT alert_rules_window_chk CHECK (window_minutes BETWEEN 5 AND 1440)
);

CREATE INDEX IF NOT EXISTS alert_rules_tenant_idx ON alert_rules (tenant_id);
-- One rule per kind per scope; two rules for the same thing would double-alert.
CREATE UNIQUE INDEX IF NOT EXISTS alert_rules_unique_scope
    ON alert_rules (tenant_id, COALESCE(campaign_id, 0), kind);

CREATE TABLE IF NOT EXISTS alerts (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    campaign_id  BIGINT      REFERENCES campaigns(id) ON DELETE SET NULL,
    rule_id      BIGINT      REFERENCES alert_rules(id) ON DELETE SET NULL,
    kind         TEXT        NOT NULL,
    severity     TEXT        NOT NULL,
    message      TEXT        NOT NULL,
    -- Kept so the console can show what actually happened, not just that
    -- something did
    value        REAL,
    threshold    REAL,
    -- pending | sent | failed | skipped (no webhook configured)
    delivery     TEXT        NOT NULL DEFAULT 'pending',
    delivery_error TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by BIGINT   REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS alerts_tenant_created_idx
    ON alerts (tenant_id, created_at DESC);
-- The unread badge counts these, so it gets its own partial index
CREATE INDEX IF NOT EXISTS alerts_unacked_idx
    ON alerts (tenant_id) WHERE acknowledged_at IS NULL;

-- Seed each existing tenant with defaults drawn from what has actually been
-- measured: p50 sits at ~2.0s and p95 at ~3.3s, so 4000 ms is a real regression
-- rather than normal variance.
INSERT INTO alert_rules (tenant_id, kind, threshold, window_minutes, severity)
SELECT t.id, v.kind, v.threshold, v.window_minutes, v.severity
  FROM tenants t
 CROSS JOIN (VALUES
        ('latency_p95',   4000.0, 15, 'warning'),
        ('error_rate',      10.0, 15, 'critical'),
        ('stale_calls',      1.0, 15, 'critical'),
        ('limit_hits',      20.0, 60, 'warning')
   ) AS v(kind, threshold, window_minutes, severity)
ON CONFLICT DO NOTHING;

COMMIT;
