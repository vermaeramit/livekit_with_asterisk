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
-- Soniox renders a spoken six-digit number as a decimal. Every digit is present
-- and in order - 2467.47 is 246747 - only the shape is wrong. The model read the
-- shape, judged it not a pincode, and asked again. Nothing was broken; nobody
-- had told it what a transcribed pincode looks like.
--
-- Fixed in two places here and a third in the agent:
--
--   1. the prompt, because the model refuses BEFORE it ever calls the tool, so
--      nothing downstream gets a chance
--   2. the tool's `pin` description, so the value that does go out is digits.
--      It read "pincode". One word. The console's own hint under that field
--      warns that a vague description produces a value the API will not match
--   3. tools.py strips non-digits from any parameter whose schema says digits
--      and nothing else - the `pattern` added below is what turns that on

BEGIN;

-- 2. What the model is told the argument is.
UPDATE campaign_tools
   SET parameters = jsonb_set(
           parameters,
           '{properties,pin}',
           jsonb_build_object(
               'type', 'string',
               -- Read by tools.py to decide it may strip non-digits. Also the
               -- honest description of the value.
               'pattern', '^[0-9]{6}$',
               'description',
               'Indian PIN code: exactly 6 digits, no spaces or punctuation. '
               'The caller dictates it and the transcript often carries a '
               'decimal point or spaces - "2467.47", "24 67 47" and "246747" '
               'all mean 246747. Remove everything that is not a digit before '
               'sending.')),
       updated_at = now()
 WHERE campaign_id = 1 AND name = 'dealer_by_pincode';

-- 1. What the model is told about reading one.
--
-- Appended at the very end rather than woven in. The prompt is the cached
-- prefix; a change near the top throws away the cache for everything after it,
-- and at 5,136 tokens that is most of the prompt. At the end only the last
-- chunk is new.
UPDATE agent_config
   SET instructions = instructions || E'\n\n'
       '## PINCODE\n'
       'पिनकोड बोलकर बताया जाता है और transcript में अक्सर दशमलव या space आ जाता है। '
       '"2467.47", "24 67 47" और "246747" — तीनों का मतलब 246747 है।\n'
       'अंकों के अलावा सब हटाकर गिनें। छह अंक बनते हों तो वही पिनकोड है, उसे सीधे इस्तेमाल करें। '
       'छह से कम या ज़्यादा हों, तभी दोबारा पूछें — और तब भी एक ही बार, '
       'फिर आगे बढ़ें या प्रतिनिधि से जोड़ें।',
       updated_at = now()
 WHERE campaign_id = 1
   AND position('## PINCODE' in instructions) = 0;   -- re-runnable

COMMIT;

SELECT jsonb_pretty(parameters) AS "pin parameter"
  FROM campaign_tools WHERE campaign_id = 1 AND name = 'dealer_by_pincode';

SELECT right(instructions, 420) AS "prompt ka aakhri hissa"
  FROM agent_config WHERE campaign_id = 1;
