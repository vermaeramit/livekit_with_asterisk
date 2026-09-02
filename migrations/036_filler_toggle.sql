-- An off switch for the fillers, so turning one off does not delete it.
--
-- Both fillers have been configurable per campaign and per tool since 024 and
-- 021. Neither had a way to stop saying it that was not "clear the box" - so
-- switching it off threw the wording away, and switching it back on meant
-- writing it again. People stop experimenting with a setting that charges them
-- for changing their mind.
--
-- Default true, and the text column decides as before: a campaign with no
-- filler text still says nothing, so this migration changes no behaviour.

ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS kb_filler_enabled BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN agent_config.kb_filler_enabled IS
    'Off = stay silent while searching, keeping kb_filler_message for later. '
    'Empty text is still silence - this only exists so the text survives.';

-- Same switch for a tool's own filler, added in 018. A column beside the text
-- rather than a key inside anything: the agent builds a tool's spec straight
-- from the row (store.load_tools does dict(r)), so a column IS the spec field.
ALTER TABLE campaign_tools
    ADD COLUMN IF NOT EXISTS filler_enabled BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN campaign_tools.filler_enabled IS
    'Off = run silently, keeping filler_message for later. Empty text is still '
    'silence - this only exists so the text survives being switched off.';

SELECT count(*) AS campaigns,
       count(*) FILTER (WHERE kb_filler_enabled) AS kb_filler_on
  FROM agent_config;
SELECT count(*) AS tools,
       count(*) FILTER (WHERE coalesce(btrim(filler_message), '') <> '')
           AS with_filler_text,
       count(*) FILTER (WHERE filler_enabled) AS filler_on
  FROM campaign_tools;
