-- Stop a dictated pincode ending the call.
--
--   docker exec -i postgres psql -U aivoice -d aivoice < fix-pincode-normalisation.sql
--
-- Call 424 died in a loop. The caller gave their pincode five times and was
-- asked for it five times:
--
--   user   1467.47।
--   agent  कृपया एक वैध 6-अंकों का पिनकोड बताएं।
--   user   2467.47।
--   agent  कृपया एक वैध 6-अंकों का पिनकोड बताएं।     (x4 more)
--
-- Soniox renders a spoken six-digit number as a decimal: 2 4 6 7 4 7 arrives as
-- "2467.47". Every digit is present and in order - only the shape is wrong.
--
-- The prompt is not missing a rule. It has one, and the model obeyed it:
--
--   If the PIN code is invalid:
--   "Could you please provide a valid 6-digit PIN code?"
--   Do not call 'dealer_by_pincode' until a valid 6-digit PIN code is available.
--
-- What it never says is what "valid" means. So this defines it, immediately
-- above the rule that uses the word - one instruction in the place the model
-- already reads, rather than a second one appended at the end that would pull
-- the other way.
--
-- The first version of this script DID append at the end, written before anyone
-- had read the prompt. It would have left the model with two contradictory
-- rules about the same thing, which is worse than the loop it was fixing.
--
-- Every edit is guarded: if the text it expects is not there exactly once, the
-- whole thing aborts and changes nothing. A blind replace on a 29,000-character
-- prompt somebody has spent weeks on is not worth the convenience.

BEGIN;

DO $$
DECLARE
    anchor  CONSTANT text := 'If the PIN code is invalid:';
    n int;
BEGIN
    SELECT count(*) INTO n
      FROM agent_config
     WHERE campaign_id = 1
       AND position(anchor in instructions) > 0;

    IF n <> 1 THEN
        RAISE EXCEPTION
            'expected exactly one campaign 1 config containing %, found % - '
            'nothing was changed. The prompt has been edited; re-read it before '
            'running this.', quote_literal(anchor), n;
    END IF;

    -- Already applied. Re-running must not stack a second copy.
    IF EXISTS (SELECT 1 FROM agent_config
                WHERE campaign_id = 1
                  AND position('Remove everything that is not a digit' in instructions) > 0) THEN
        RAISE NOTICE 'pincode rule already present - prompt left alone';
    ELSE
        UPDATE agent_config
           SET instructions = replace(
                   instructions,
                   anchor,
                   'The PIN code is dictated aloud, so the transcript often '
                   'carries a decimal point or spaces. "2467.47", "24 67 47" '
                   'and "246747" all mean 246747. Remove everything that is '
                   'not a digit, then count. Six digits means the PIN is '
                   'valid - use it as it is and do not ask again.' || E'\n\n'
                   || anchor),
               updated_at = now()
         WHERE campaign_id = 1;
    END IF;
END $$;

-- What the model is told the argument is. It read "pincode" - one word - and
-- the console's own hint under that field warns that a vague description
-- produces a value the API will not match.
--
-- The `pattern` is not decoration: agent/tools.py reads it and will strip
-- characters the schema forbids, but only where the schema says digits and
-- nothing else.
UPDATE campaign_tools
   SET parameters = jsonb_set(
           parameters, '{properties,pin}',
           jsonb_build_object(
               'type', 'string',
               'pattern', '^[0-9]{6}$',
               'description',
               'Indian PIN code: exactly 6 digits, no spaces or punctuation. '
               'The caller dictates it and the transcript often carries a '
               'decimal point or spaces - "2467.47", "24 67 47" and "246747" '
               'all mean 246747. Remove everything that is not a digit before '
               'sending.')),
       updated_at = now()
 WHERE campaign_id = 1 AND name = 'dealer_by_pincode';

COMMIT;

SELECT jsonb_pretty(parameters->'properties'->'pin') AS "pin parameter"
  FROM campaign_tools WHERE campaign_id = 1 AND name = 'dealer_by_pincode';

SELECT length(instructions) AS chars,
       substring(instructions
                 from greatest(position('The PIN code is dictated aloud' in instructions) - 60 , 1)
                 for 420) AS "prompt, around the change"
  FROM agent_config WHERE campaign_id = 1;
