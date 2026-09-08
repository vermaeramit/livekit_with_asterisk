-- What the prompt used to say.
--
-- Every change already lands in config_audit with the full text on both sides,
-- so the history exists. It cannot be used for this: an audit trail you can
-- delete from is not an audit trail, and being able to clear out versions
-- nobody wants was half the request.
--
-- So this is a separate, editable list, and config_audit stays untouched.
--
-- THE PREVIOUS TEXT IS WHAT IS KEPT, NOT THE NEW ONE
--
-- The current prompt is in the editor; nobody needs to restore what they are
-- looking at. A row here is a prompt you can go BACK to, which is the only
-- reason to keep one.

CREATE TABLE IF NOT EXISTS prompt_versions (
    id           BIGSERIAL PRIMARY KEY,
    campaign_id  BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    tenant_id    BIGINT,
    instructions TEXT   NOT NULL,
    -- Counted at save time with the model's own tokeniser. Shown beside each
    -- version because prompt size is what decides how many calls run at once -
    -- 17,000 tokens a turn is what put the concurrency ceiling at six.
    n_tokens     INTEGER,
    -- Who saved it, denormalised. A version outlives the account that made it,
    -- and "changed by user 14" answers nothing a year later.
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS prompt_versions_campaign_idx
    ON prompt_versions (campaign_id, created_at DESC);

-- Seed each campaign with what its prompt says today, so the feature is not
-- empty until somebody happens to edit. Without this the first edit would show
-- one version and no way to see that anything came before it.
INSERT INTO prompt_versions (campaign_id, tenant_id, instructions, created_by)
SELECT ac.campaign_id, c.tenant_id, ac.instructions, 'before version history'
  FROM agent_config ac JOIN campaigns c ON c.id = ac.campaign_id
 WHERE coalesce(ac.instructions, '') <> ''
   AND NOT EXISTS (SELECT 1 FROM prompt_versions v
                    WHERE v.campaign_id = ac.campaign_id);

SELECT campaign_id, count(*) AS versions FROM prompt_versions
 GROUP BY 1 ORDER BY 1;
