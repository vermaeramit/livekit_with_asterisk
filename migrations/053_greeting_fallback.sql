-- A different greeting for when the dialler did not say who is calling.
--
-- `{{cus_name|आप}}` can only swap a word, and the word is the wrong shape: "क्या
-- मेरी बात आप से हो रही है?" is not what anybody would say. What is needed is a
-- different SENTENCE - ask for the name instead of using it.
--
-- Measured before building it, over the last 200 calls:
--
--   dialer.lead_id      200
--   dialer.call_unique  200
--   dialer.calltype     200
--   dialer.cus_name      11   <- 5.5%
--   dialer.sr_id         11
--   dialer.modalname     11
--
-- So the branch without a name is the COMMON one, by a long way, and a campaign
-- personalising its greeting is personalising 5% of its calls.

ALTER TABLE agent_config
    -- NULL or empty = no alternative, and the greeting is used as it is today,
    -- placeholders falling back to their own defaults. Nothing changes for any
    -- existing campaign.
    ADD COLUMN IF NOT EXISTS greeting_fallback TEXT;

COMMENT ON COLUMN agent_config.greeting_fallback IS
    'Spoken instead of `greeting` when a placeholder in `greeting` has no value '
    'for this call - typically the dialler not sending a name. NULL = use '
    'greeting regardless. Also used by the web widget, which has no dialler at '
    'all.';

-- ── The latency this also fixes ─────────────────────────────────────────────
-- The greeting is pre-rendered and cached, keyed on a hash of its text - but
-- caching is skipped entirely when the greeting contains `{{`, because one
-- cache entry per caller is a disk leak rather than a cache.
--
-- The consequence nobody had noticed: a campaign that personalises its greeting
-- renders 7.2 seconds of speech LIVE on every call, and the caller waits for
-- the first byte every time. Measured at 1458-1519 ms against 619-701 ms warm.
--
-- With this column, cacheability is decided on the greeting actually CHOSEN. On
-- the 95% of calls with no name, the alternative has no placeholder in it and is
-- served from the cache - so the change gives those calls an instant greeting,
-- and lets the TTS connection warm up behind it instead of landing on the
-- caller's first question.

-- Which campaigns would be affected, and whether they have anything to fall
-- back to yet. Nothing is broken today; this is what to fill in.
SELECT name,
       greeting LIKE '%{{%'                          AS greeting_personalised,
       coalesce(btrim(greeting_fallback), '') <> ''   AS has_alternative
  FROM agent_config
 ORDER BY 2 DESC, 1;
