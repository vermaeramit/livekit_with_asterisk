-- Which region a provider key belongs to.
--
-- Soniox runs the same API in four regions - US, EU, Japan, India - on four
-- sets of hostnames, and a key belongs to exactly ONE of them: the region is
-- chosen when a project is created and the keys it issues work nowhere else.
-- So the region is a property OF THE KEY, not of the campaign, and it lives in
-- this row beside the key it belongs to. Resolving them separately would let a
-- campaign's key meet another scope's region and produce a pair that exists
-- nowhere.
--
-- Why it exists at all: on 21-23 Sep 2026 Soniox TTS stalled for seconds at a
-- time, mid-sentence, on both servers and on a laptop on three networks. Soniox
-- traced it to the path from here to their US region and opened the India
-- region for this account. Measured from a laptop on 23 Sep: US first audio
-- 400-900 ms with 8 of 10 samples stalling, India 210-272 ms with 0 of 10.
--
-- NULL means "the provider's own default host", which is what every existing
-- row gets and what every existing call already uses. Nothing changes for a key
-- that does not set this. It is deliberately not DEFAULT 'us': a Sarvam or
-- OpenAI key has no region, and writing one there would be a claim this schema
-- cannot back up.

ALTER TABLE provider_keys ADD COLUMN IF NOT EXISTS region TEXT;

ALTER TABLE provider_keys DROP CONSTRAINT IF EXISTS provider_keys_region_chk;
ALTER TABLE provider_keys ADD CONSTRAINT provider_keys_region_chk
    CHECK (region IS NULL OR region IN ('us', 'eu', 'jp', 'in'));

COMMENT ON COLUMN provider_keys.region IS
    'Provider region this key was issued in - soniox only today: us, eu, jp, '
    'in. NULL = the provider default host, which is what every key used before '
    'migration 058. A key only works in its own region, so this travels with '
    'the key and is never resolved on its own.';
