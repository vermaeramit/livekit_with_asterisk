-- One-time provider prices, filled in on 29 August 2026.
--
-- Kept out of the migrations on purpose. A migration runs on every deployment
-- forever, so a price in one is a price that reinstates itself long after it
-- stopped being true. This is a script somebody ran once, on a date, and the
-- date is written on every row.
--
--   docker exec -i postgres psql -U aivoice -d aivoice < seed-rates.sql
--
-- Re-running it is safe: each row replaces itself rather than duplicating.
-- Requires migration 029, which added the currency column.
--
--
-- WHERE THESE NUMBERS CAME FROM
--
-- OpenAI: developers.openai.com/api/docs/pricing, read 29 Aug 2026.
--
-- Sarvam: docs.sarvam.ai/api-reference-docs/pricing, read 29 Aug 2026.
--   Speech to text  Rs 30/hour, billed per second
--   Text to speech  Rs 30 per 10,000 characters  ->  Rs 3,000 per million
--
-- Soniox: solved from the account's own usage page rather than a price list,
-- which is better evidence - it is what was actually charged. Three models gave
-- three equations and they agree to the last cent:
--
--   text tokens, in or out   $4.00  per 1M
--   STT input audio          $2.00  per 1M audio tokens
--   TTS output audio        $21.50  per 1M audio tokens
--
--   stt-rt-v5   69,780 audio x $2.00 + 2,072 text x $4.00 = $0.147848  billed $0.147848
--   tts-rt-v2   28,991 audio x $21.50                     = $0.623306  billed $0.623306
--
--
-- WHY SARVAM'S ROWS SAY INR
--
-- Because Sarvam charges rupees, and will still charge the same rupees after
-- the exchange rate moves. Converting to dollars here would have made its rupee
-- cost drift every time somebody edited that rate - silently, and in the
-- direction nobody would think to check.
--
--
-- THE ONE APPROXIMATION, AND IT IS DELIBERATE
--
-- Soniox bills audio AND text tokens. We record audio seconds and not the
-- transcript's token count, so the text share is folded into the per-hour
-- figure below rather than dropped:
--
--   STT   text is 21% of the cost   $0.176056 / 2.33 h = $0.07556 per hour
--   TTS   text is 12% of the cost   $0.711883 / 0.96 h = $0.74154 per hour
--
-- That holds because transcript length tracks audio length. It would stop
-- holding if a campaign started sending long `context` hints, which are input
-- text tokens with no audio to scale against.
--
-- Sarvam needs no such fudge: it prices the two things we already count.

INSERT INTO provider_rates (provider, model, kind, unit, price, currency, note) VALUES
    ('openai', 'gpt-4.1-mini', 'llm_input',   'per_million', 0.40,    'USD', 'openai pricing page, 29 Aug 2026'),
    ('openai', 'gpt-4.1-mini', 'llm_cached',  'per_million', 0.10,    'USD', 'openai pricing page, 29 Aug 2026'),
    ('openai', 'gpt-4.1-mini', 'llm_output',  'per_million', 1.60,    'USD', 'openai pricing page, 29 Aug 2026'),

    -- Priced per audio hour because that is the unit we record. See above for
    -- the text-token share folded into it.
    ('soniox', 'stt-rt-v5',    'stt_seconds', 'per_hour',    0.07556, 'USD', 'solved from account usage, 29 Aug 2026 (incl. transcript tokens)'),
    ('soniox', 'tts-rt-v2',    'tts_seconds', 'per_hour',    0.74154, 'USD', 'solved from account usage, 29 Aug 2026 (incl. input text tokens)'),
    -- Retired 31 Aug 2026, but there are calls on it in the history.
    ('soniox', 'tts-rt-v1',    'tts_seconds', 'per_hour',    0.73039, 'USD', 'solved from account usage, 29 Aug 2026 (incl. input text tokens)'),

    -- No model named: Sarvam prices the service, not the model. Add a
    -- model-specific row if that stops being true - it wins over this one.
    ('sarvam', NULL,           'stt_seconds',    'per_hour',    30.00,   'INR', 'sarvam docs, 29 Aug 2026 - Rs 30/hour'),
    ('sarvam', NULL,           'tts_characters', 'per_million', 3000.00, 'INR', 'sarvam docs, 29 Aug 2026 - Rs 30 per 10K characters')

ON CONFLICT (provider, coalesce(model, ''), kind) DO UPDATE
    SET unit = EXCLUDED.unit,
        price = EXCLUDED.price,
        currency = EXCLUDED.currency,
        note = EXCLUDED.note,
        updated_at = now();

SELECT provider, coalesce(model, '(any)') AS model, kind, unit, price, currency, note
  FROM provider_rates
 ORDER BY provider, model, kind;
