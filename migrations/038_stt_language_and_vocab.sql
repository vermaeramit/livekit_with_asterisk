-- Two things Soniox was already offering that nothing was taking.
--
--
-- 1. THE LANGUAGE WE WERE THROWING AWAY
--
-- The plugin defaults enable_language_identification to true and we never
-- turned it off, so every transcript has arrived carrying a detected language
-- since the day Soniox went in. Nothing read it.
--
-- What the console showed against a call was agent_config.language - the
-- CAMPAIGN's setting. Every call said hi-IN because the campaign says hi-IN,
-- which is a fact about our configuration and not about the caller.
--
-- Stored as characters per language rather than a winner, because these calls
-- are Hinglish and the mix is the interesting part. Turn counts would score a
-- two-word English aside the same as a full Hindi sentence.
ALTER TABLE calls
    ADD COLUMN IF NOT EXISTS detected_languages JSONB;

COMMENT ON COLUMN calls.detected_languages IS
    'Characters of final transcript per language Soniox identified, e.g. '
    '{"hi": 812, "en": 233}. NULL for calls before this existed, and for any '
    'provider that does not identify languages.';


-- 2. WORDS THE MODEL HAS NEVER HEARD OF
--
-- STT rendered "Splendor Plus Flex" as "Lender Plus Flex", the knowledge base
-- then matched a different bike at 0.57, and the caller was answered about the
-- wrong motorcycle. No prompt fixes that: the wrong words were already in the
-- transcript before the model saw them.
--
-- Soniox takes a term list. Product names, dealer names, anything the caller
-- says that a general model has no reason to know.
ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS stt_context_terms JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN agent_config.stt_context_terms IS
    'Terms passed to the STT as context, so it spells them the way you do. '
    'Soniox only; other providers ignore it.';

SELECT count(*) AS calls,
       count(*) FILTER (WHERE detected_languages IS NOT NULL) AS with_language
  FROM calls;
SELECT name, jsonb_array_length(stt_context_terms) AS terms FROM agent_config;
