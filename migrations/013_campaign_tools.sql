-- =============================================================================
-- 013 — Per-campaign HTTP tools
--
-- The agent can already search a knowledge base and hand off to a human. This
-- lets a campaign give it more: look up a service history, check a warranty,
-- book an appointment - by calling the client's own API mid-conversation.
--
-- Two tables, because they answer different questions. campaign_tools is
-- configuration and changes rarely. tool_invocations is evidence and grows with
-- every call: what the agent sent, what came back, how long it took. Writes are
-- in scope, so "why does this customer have two appointments" is a question that
-- WILL be asked, and it is unanswerable without a record.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS campaign_tools (
    id          BIGSERIAL   PRIMARY KEY,
    campaign_id BIGINT      NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    -- Denormalised like calls.tenant_id, for the same reason: every list and
    -- permission check in the console filters by tenant.
    tenant_id   BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    -- What the model calls. Must look like a Python identifier because that is
    -- what livekit-agents turns it into.
    name        TEXT        NOT NULL,
    -- WHEN to call it. This is the field that decides whether the tool is used
    -- correctly - the model reads it and nothing else. A vague description is
    -- the most common reason a tool is called at the wrong moment, or never.
    description TEXT        NOT NULL,
    -- JSON Schema for the arguments, exactly as the model will receive it.
    parameters  JSONB       NOT NULL DEFAULT '{"type":"object","properties":{}}',

    method      TEXT        NOT NULL DEFAULT 'GET',
    url         TEXT        NOT NULL,
    -- Non-secret headers only. Anything sensitive goes in auth_value_enc.
    headers     JSONB,
    auth_header TEXT,
    -- Encrypted with the same Fernet key as provider_keys - see agent/crypto.py.
    -- Never returned by the API; the console shows a hint.
    auth_value_enc TEXT,
    auth_value_hint TEXT,
    -- {{arg}} placeholders, substituted from the model's arguments.
    body_template  TEXT,

    -- A tool call happens mid-conversation, inside a ~2s turn budget. Past this
    -- the caller is listening to silence, which is worse than a tool that
    -- failed - so the default is deliberately short and the cap is enforced.
    timeout_ms  INTEGER     NOT NULL DEFAULT 2500,
    -- An API returning 50 KB of JSON would put all of it in the next prompt:
    -- slow, expensive, and more likely to confuse the model than help it.
    max_response_bytes INTEGER NOT NULL DEFAULT 8192,
    -- Optional dotted path into the response, e.g. "data.customer". Without it
    -- the whole body goes to the model, truncated to max_response_bytes.
    response_path TEXT,

    enabled     BOOLEAN     NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Same shape the model's function-calling API demands. Rejecting it here is
    -- kinder than a provider rejecting the whole request mid-call.
    CONSTRAINT campaign_tools_name_chk
        CHECK (name ~ '^[a-z][a-z0-9_]{2,47}$'),
    CONSTRAINT campaign_tools_method_chk
        CHECK (method IN ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')),
    -- 10s would mean ten seconds of silence. The ceiling is a product decision,
    -- not a technical one.
    CONSTRAINT campaign_tools_timeout_chk
        CHECK (timeout_ms BETWEEN 200 AND 8000),
    CONSTRAINT campaign_tools_size_chk
        CHECK (max_response_bytes BETWEEN 256 AND 65536),
    CONSTRAINT campaign_tools_url_chk
        CHECK (url ~* '^https?://'),
    -- A description the model cannot act on is worse than no tool.
    CONSTRAINT campaign_tools_desc_chk
        CHECK (length(btrim(description)) >= 10),
    CONSTRAINT campaign_tools_tenant_fk
        FOREIGN KEY (campaign_id, tenant_id)
        REFERENCES campaigns (id, tenant_id) ON DELETE CASCADE
);

-- Two tools with the same name in one campaign means the model's call is
-- ambiguous and which one runs depends on row order.
CREATE UNIQUE INDEX IF NOT EXISTS campaign_tools_name_unique
    ON campaign_tools (campaign_id, name);

CREATE TABLE IF NOT EXISTS tool_invocations (
    id          BIGSERIAL   PRIMARY KEY,
    call_id     BIGINT      REFERENCES calls(id) ON DELETE CASCADE,
    tool_id     BIGINT      REFERENCES campaign_tools(id) ON DELETE SET NULL,
    -- Kept by name too: a tool can be deleted, and the record of what it did
    -- must outlive it.
    name        TEXT        NOT NULL,
    arguments   JSONB,
    status_code INTEGER,
    duration_ms INTEGER,
    -- NULL on success. Timeouts land here, and they are the interesting case:
    -- the caller heard silence and the model got nothing.
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tool_invocations_call_idx
    ON tool_invocations (call_id, created_at);
-- "Which calls had a tool fail" is the question worth answering quickly.
CREATE INDEX IF NOT EXISTS tool_invocations_error_idx
    ON tool_invocations (created_at DESC) WHERE error IS NOT NULL;

COMMIT;
