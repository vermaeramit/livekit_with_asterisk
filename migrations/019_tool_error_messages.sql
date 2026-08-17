-- =============================================================================
-- 019 — What to say when a tool does not return what was hoped for
--
-- Every non-200 was treated as a failure and handed to the model as "the lookup
-- returned an error, tell the caller you could not retrieve it". Observed live:
--
--   caller gives pincode 485056
--   dealer_by_pincode -> HTTP 404 (no dealers in that area)
--   agent: "अभी dealer की जानकारी नहीं मिल पा रही है, थोड़ी देर में confirm हो जाएगी"
--
-- That is the wrong thing to tell someone. The lookup worked perfectly; the
-- answer was "there is no dealer near you". The caller should have been asked
-- for another pincode, and instead was told the system was having trouble.
--
-- 404 and 500 mean completely different things to a caller. So does a timeout.
-- A single message cannot cover them, and the right words are a decision about
-- the campaign rather than something this code can know.
--
-- Shape: {"404": "...", "500": "...", "timeout": "...", "default": "..."}
-- Looked up by exact status, then "default", then the built-in text. NULL keeps
-- today's behaviour exactly.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE campaign_tools
    ADD COLUMN IF NOT EXISTS error_messages JSONB;

-- An object, not an array. A list would need positions to mean something, and
-- the thing being looked up is a status code.
ALTER TABLE campaign_tools
    DROP CONSTRAINT IF EXISTS campaign_tools_error_messages_chk;
ALTER TABLE campaign_tools
    ADD CONSTRAINT campaign_tools_error_messages_chk
    CHECK (error_messages IS NULL
           OR jsonb_typeof(error_messages) = 'object');

COMMIT;
