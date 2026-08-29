-- One-time provider prices, filled in on 29 August 2026.
--
-- Kept out of migration 028 on purpose. A migration runs on every deployment
-- forever, so a price in one is a price that reinstates itself long after it
-- stopped being true. This is a script somebody ran once, on a date, and the
-- date is written on every row.
--
--   docker exec -i postgres psql -U aivoice -d aivoice < seed-rates.sql
--
-- Re-running it is safe: each row replaces itself rather than duplicating.
--
--
-- WHERE THESE NUMBERS CAME FROM
--
-- OpenAI: developers.openai.com/api/docs/pricing, read 29 Aug 2026.
--
-- Soniox: solved from the account's own usage page rather than a price list,
-- which is better evidence - it is what was actually charged. Three models gave
-- three equations and they agree to the last cent:
--
--   text tokens, in or out   $4.00  per 1M      (tts-rt-v1 and v2 input,
--                                                stt-rt-v5 output)
--   STT input audio          $2.00  per 1M audio tokens
--   TTS output audio        $21.50  per 1M audio tokens
--
--   stt-rt-v5   69,780 audio x $2.00 + 2,072 text x $4.00 = $0.147848  billed $0.147848
--   tts-rt-v2   28,991 audio x $21.50                     = $0.623306  billed $0.623306
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
-- text tokens with no audio to scale against. Worth remembering before anyone
-- turns that on.
--
--
-- NOT SET HERE
--
-- Sarvam, used by campaigns 2 and 3. There is no usage on the account to solve
-- from and I will not copy a number I have not verified into a bill.
--
-- The exchange rate, because the market mid-rate is not what you pay - your
-- bank's is, and only you know it. Set it on the Provider rates page.

INSERT INTO provider_rates (provider, model, kind, unit, usd_price, note) VALUES
    ('openai', 'gpt-4.1-mini', 'llm_input',      'per_million', 0.40,    'openai pricing page, 29 Aug 2026'),
    ('openai', 'gpt-4.1-mini', 'llm_cached',     'per_million', 0.10,    'openai pricing page, 29 Aug 2026'),
    ('openai', 'gpt-4.1-mini', 'llm_output',     'per_million', 1.60,    'openai pricing page, 29 Aug 2026'),

    -- Priced per audio hour because that is the unit we record. See above for
    -- the text-token share folded into it.
    ('soniox', 'stt-rt-v5',    'stt_seconds',    'per_hour',    0.07556, 'solved from account usage, 29 Aug 2026 (incl. transcript tokens)'),
    ('soniox', 'tts-rt-v2',    'tts_seconds',    'per_hour',    0.74154, 'solved from account usage, 29 Aug 2026 (incl. input text tokens)'),
    -- Retired 31 Aug 2026, but there are calls on it in the history.
    ('soniox', 'tts-rt-v1',    'tts_seconds',    'per_hour',    0.73039, 'solved from account usage, 29 Aug 2026 (incl. input text tokens)')

ON CONFLICT (provider, coalesce(model, ''), kind) DO UPDATE
    SET unit = EXCLUDED.unit,
        usd_price = EXCLUDED.usd_price,
        note = EXCLUDED.note,
        updated_at = now();

-- What is now priced, and what is still blank.
SELECT provider, coalesce(model, '(any)') AS model, kind, unit, usd_price, note
  FROM provider_rates
 ORDER BY provider, model, kind;
