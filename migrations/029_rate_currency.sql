-- Some providers bill in rupees.
--
-- Sarvam charges Rs 30/hour for speech to text and Rs 30 per 10,000 characters
-- for speech, in rupees, always. OpenAI and Soniox charge dollars.
--
-- Holding Sarvam's price as a dollar figure converted at today's rate would
-- have meant its RUPEE cost moved every time the exchange rate was edited -
-- which it never does. The currency belongs to the rate, not to the display.

ALTER TABLE provider_rates
    ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'USD';

ALTER TABLE provider_rates
    DROP CONSTRAINT IF EXISTS provider_rates_currency_check;
ALTER TABLE provider_rates
    ADD CONSTRAINT provider_rates_currency_check CHECK (currency IN ('USD', 'INR'));

-- usd_price is now a misnomer for half the rows. Renamed rather than left to be
-- misread: a column called usd_price holding rupees is exactly the sort of
-- thing that survives until it appears on an invoice.
ALTER TABLE provider_rates RENAME COLUMN usd_price TO price;

COMMENT ON COLUMN provider_rates.price IS
    'Price for one `unit`, in `currency`. Not always USD - Sarvam bills rupees.';
COMMENT ON COLUMN provider_rates.currency IS
    'What the provider actually charges in. Converted only for display.';
