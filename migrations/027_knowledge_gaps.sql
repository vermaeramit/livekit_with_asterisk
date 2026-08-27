-- What the bot could not answer, so it can be taught.
--
-- Three signals, all of them already flowing through code we control and none
-- of them costing anything:
--
--   kb_miss     - the caller asked, we searched, nothing came back
--   kb_weak     - something came back, but only just: RidgeMax MR scored 0.34
--                 against a 0.25 floor, and answered from a chunk that barely
--                 matched
--   tool_failed - a lookup the caller was waiting on returned 4xx or timed out
--
-- Deliberately NOT inferred from what the agent said. The grounding rules make
-- it say it does not know, but the wording varies by language and by turn, and
-- a feature that decides what to teach the bot cannot be built on a string
-- match against Hindi.
--
-- One row per occurrence, not per question. The console groups them, because
-- "asked 20 times" is what tells you which gap to fill first - and keeping the
-- occurrences keeps the link back to the calls they came from.

CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    BIGINT      NOT NULL REFERENCES tenants(id)  ON DELETE CASCADE,
    campaign_id  BIGINT      REFERENCES campaigns(id) ON DELETE CASCADE,
    call_id      BIGINT      REFERENCES calls(id)     ON DELETE SET NULL,
    -- kb_miss | kb_weak | tool_failed
    kind         TEXT        NOT NULL,
    -- What was actually asked for, in the words it was asked in. This is the
    -- field somebody reads to decide what to write.
    query        TEXT        NOT NULL,
    -- Lowercased and collapsed, for grouping only. Stored rather than computed
    -- so the index is usable and the grouping cannot drift between queries.
    query_key    TEXT        NOT NULL,
    -- The best score we managed, on a kb_weak. NULL elsewhere.
    best_score   REAL,
    -- Free text: the scores, the status code, whatever explains it.
    detail       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Acknowledging says "handled". A later occurrence of the same question
    -- creates a new row and reappears, which is correct: it is fresh evidence
    -- that the gap is still there.
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by BIGINT   REFERENCES users(id) ON DELETE SET NULL,
    -- What was done about it. "Uploaded the Splendor Flex brochure."
    note         TEXT
);

CREATE INDEX IF NOT EXISTS knowledge_gaps_tenant_created_idx
    ON knowledge_gaps (tenant_id, created_at DESC);
-- The page's default view, and the badge count
CREATE INDEX IF NOT EXISTS knowledge_gaps_open_idx
    ON knowledge_gaps (tenant_id, campaign_id) WHERE acknowledged_at IS NULL;
-- Grouping by question is the whole point of the page
CREATE INDEX IF NOT EXISTS knowledge_gaps_key_idx
    ON knowledge_gaps (campaign_id, query_key);
