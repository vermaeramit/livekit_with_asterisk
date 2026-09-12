-- Let the dialler ask how many more calls a campaign can take.
--
-- The dialler team needs one number before placing a call: is there room. They
-- have that number already in their own head - they know how many they sent -
-- but not how many are still up, and guessing is what the hold queue exists to
-- absorb. Asking is cheaper than queueing.
--
-- Everything the answer needs is already here: agent_config.max_parallel_calls
-- and the count of calls with no ended_at. The System page has been running that
-- exact query since 10 Sep. What is missing is a way in - the dialler's own
-- campaign id, and something to authenticate with.

-- ── The dialler's ids ───────────────────────────────────────────────────────
-- A campaign has MANY of them: the dialler splits one of our campaigns across
-- several of theirs, and the split is theirs to make. So this is a table rather
-- than a column.
--
-- UNIQUE on dialler_campaign_id, globally and not per tenant. The request
-- carries that id and nothing else, and the whole job of this table is to turn
-- it into exactly one campaign. Two campaigns claiming one id would make the
-- answer depend on which row came back first.
CREATE TABLE IF NOT EXISTS campaign_dialler_ids (
    id                  BIGSERIAL PRIMARY KEY,
    campaign_id         BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,

    -- THEIRS, not ours. Both ids live in this row and confusing them would be a
    -- lookup that silently answers about the wrong campaign, so neither is
    -- called just "id".
    dialler_campaign_id TEXT   NOT NULL,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          BIGINT REFERENCES users(id) ON DELETE SET NULL,

    CONSTRAINT campaign_dialler_ids_ref_chk
        CHECK (length(btrim(dialler_campaign_id)) BETWEEN 1 AND 128)
);

CREATE UNIQUE INDEX IF NOT EXISTS campaign_dialler_ids_ref_key
    ON campaign_dialler_ids (dialler_campaign_id);

CREATE INDEX IF NOT EXISTS campaign_dialler_ids_campaign_idx
    ON campaign_dialler_ids (campaign_id);

COMMENT ON COLUMN campaign_dialler_ids.dialler_campaign_id IS
    'The DIALLER''s id for its campaign, as it will send it. Unique across every '
    'tenant: the capacity request carries this and nothing else.';

-- ── The key the dialler presents ────────────────────────────────────────────
-- Per tenant. Per campaign would make the dialler hold one key per campaign and
-- remember which goes with which id - and a campaign here has several ids, so
-- that mapping would be theirs to get wrong. One key for everything would mean a
-- leak exposing every client and a rotation stopping all of them at once.
--
-- HASHED, not encrypted, which is the opposite of provider_keys. Those have to
-- be decrypted because they are presented to OpenAI; this one is only ever
-- COMPARED. Shown once when it is generated and never again - so a copy of this
-- database yields no usable key at all.
--
-- sha256 and deliberately not bcrypt. The key is 32 random bytes; there is no
-- dictionary to run against it and nothing for a slow hash to buy. And this is
-- verified on a polled endpoint, where a deliberately slow hash would be a cost
-- paid on every request to defend against an attack that cannot happen.
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS api_key_hash   TEXT,
    -- Last four characters, so the console can say WHICH key is set without
    -- being able to show it. Same reasoning as provider_keys' hint.
    ADD COLUMN IF NOT EXISTS api_key_hint   TEXT,
    ADD COLUMN IF NOT EXISTS api_key_set_at TIMESTAMPTZ;

COMMENT ON COLUMN tenants.api_key_hash IS
    'sha256 of the dialler API key. NULL = this client has no key and the '
    'capacity endpoint refuses everything for it. Never reversible: the key is '
    'displayed once at generation and nowhere else.';

-- Lookup is by hash on every request, so it gets an index. Partial, because most
-- tenants will never have a key and a NULL is not a candidate.
CREATE INDEX IF NOT EXISTS tenants_api_key_hash_idx
    ON tenants (api_key_hash) WHERE api_key_hash IS NOT NULL;

-- ── Counting open calls, cheaply ────────────────────────────────────────────
-- calls_campaign_idx (migration 001) covers campaign_id across the WHOLE table,
-- which grows forever. This one holds only the rows with no ended_at - a handful
-- at any moment, however many calls have ever been made - and that is the only
-- set this endpoint ever counts.
--
-- It matters because the dialler polls this before placing calls, so it is the
-- most frequently run query in the system rather than the rarest.
CREATE INDEX IF NOT EXISTS calls_open_campaign_idx
    ON calls (campaign_id) WHERE ended_at IS NULL;

-- ── What this deliberately does NOT enforce ─────────────────────────────────
-- "A campaign with dialler ids must have max_parallel_calls set" is a real
-- invariant and there is no CHECK for it, because it spans two tables -
-- campaign_dialler_ids here and agent_config.max_parallel_calls there. A CHECK
-- cannot see across a row boundary and a trigger that could would be the only
-- cross-table trigger in this schema.
--
-- So the API layer enforces it, in BOTH directions: refusing a dialler id on a
-- campaign with no limit, and refusing to clear the limit on a campaign that has
-- dialler ids. The second is the one that gets forgotten, and it is the one that
-- would leave the endpoint answering with a capacity of null forever.

-- Nothing changes today: no campaign has a dialler id and no client has a key,
-- so the endpoint refuses every request until somebody sets both up.
SELECT (SELECT count(*) FROM campaign_dialler_ids)              AS dialler_ids,
       (SELECT count(*) FROM tenants WHERE api_key_hash IS NOT NULL) AS clients_with_key,
       (SELECT count(*) FROM agent_config WHERE max_parallel_calls IS NOT NULL)
                                                                AS campaigns_with_limit;
