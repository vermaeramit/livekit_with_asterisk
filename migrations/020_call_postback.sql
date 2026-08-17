-- =============================================================================
-- 020 — Push what the call produced to the client's API
--
-- The conversation knows things nothing here records: which payment the caller
-- chose, whether they want to exchange, how the call actually went. The old
-- prompt tried to produce them by writing a JSON document as its reply, which
-- meant a caller heard `{ "customer_name": "", "uses":` read aloud and nothing
-- was captured anyway.
--
-- Two tables, because they answer two different questions.
--
-- CONFIG lives on the campaign: where to send it, what to extract, how hard to
-- retry. The field list is per campaign on purpose - it changes with the script,
-- and it drives BOTH the extraction and the payload shape. One definition, so
-- the two cannot drift apart.
--
-- EVIDENCE lives in call_postbacks: one row per call, written BEFORE any
-- delivery is attempted. Same rule as `alerts` in migration 005 - the record is
-- the source of truth and delivery is best effort, so a client API that is down
-- costs a retry rather than the data.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

-- --- config ----------------------------------------------------------------

ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS postback_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS postback_url TEXT;
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS postback_auth_header TEXT;
-- Encrypted with the same Fernet key as provider keys and tool auth values.
-- Never returned by the API; the console shows a four-character hint.
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS postback_auth_value_enc TEXT;
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS postback_auth_value_hint TEXT;

-- [{"key": "payment_mode", "type": "string", "description": "cash or finance"}]
--
-- An ARRAY, not an object: the order is what the console shows and what the
-- payload documents, and an object would leave that to chance.
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS postback_fields JSONB;

-- Whether to include the full transcript. Off by default - it is the largest
-- part of the payload by far and most APIs do not want it.
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS postback_include_transcript BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS postback_max_attempts INTEGER NOT NULL DEFAULT 5;
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS postback_retry_after_sec INTEGER NOT NULL DEFAULT 60;

ALTER TABLE agent_config
    DROP CONSTRAINT IF EXISTS agent_config_postback_attempts_chk;
ALTER TABLE agent_config
    ADD CONSTRAINT agent_config_postback_attempts_chk
    CHECK (postback_max_attempts BETWEEN 1 AND 20);

ALTER TABLE agent_config
    DROP CONSTRAINT IF EXISTS agent_config_postback_retry_chk;
ALTER TABLE agent_config
    ADD CONSTRAINT agent_config_postback_retry_chk
    CHECK (postback_retry_after_sec BETWEEN 10 AND 3600);

ALTER TABLE agent_config
    DROP CONSTRAINT IF EXISTS agent_config_postback_url_chk;
ALTER TABLE agent_config
    ADD CONSTRAINT agent_config_postback_url_chk
    CHECK (postback_url IS NULL OR postback_url ~* '^https?://');

-- --- evidence --------------------------------------------------------------

CREATE TABLE IF NOT EXISTS call_postbacks (
    id          BIGSERIAL   PRIMARY KEY,
    -- One postback per call. A retry updates this row; it never makes another,
    -- or a flapping API would deliver the same call five times.
    call_id     BIGINT      NOT NULL UNIQUE
                            REFERENCES calls(id) ON DELETE CASCADE,
    campaign_id BIGINT      REFERENCES campaigns(id) ON DELETE SET NULL,

    -- Exactly what was sent, or will be. Kept even after success: "what did we
    -- tell them about this call" is asked months later, and re-deriving it from
    -- a transcript is not an answer.
    payload     JSONB       NOT NULL,

    -- pending | sent | failed | skipped
    -- `failed` means the attempts are used up. `skipped` means extraction
    -- produced nothing worth sending, which is not an error.
    status      TEXT        NOT NULL DEFAULT 'pending',
    attempts    INTEGER     NOT NULL DEFAULT 0,
    -- The last response, truncated. A 422 body naming the field it disliked is
    -- the difference between a fix and a guess.
    last_error  TEXT,
    last_status_code INTEGER,

    -- NULL once the row is finished. The sweeper looks at nothing else.
    next_attempt_at TIMESTAMPTZ DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at     TIMESTAMPTZ,

    CONSTRAINT call_postbacks_status_chk
        CHECK (status IN ('pending', 'sent', 'failed', 'skipped'))
);

-- The sweeper's only query: what is due now. Partial, because a finished row is
-- never looked at again and this table grows with every call.
CREATE INDEX IF NOT EXISTS call_postbacks_due_idx
    ON call_postbacks (next_attempt_at)
 WHERE next_attempt_at IS NOT NULL;

-- "Which calls did not make it to the client" - the question worth answering
-- quickly, and the one someone asks in a hurry.
CREATE INDEX IF NOT EXISTS call_postbacks_failed_idx
    ON call_postbacks (created_at DESC) WHERE status = 'failed';

COMMIT;
