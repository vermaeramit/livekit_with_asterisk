-- =============================================================================
-- 014 — The URL a tool actually requested
--
-- tool_invocations already stored the arguments the model chose. That turned
-- out not to be enough. A tool went out with `pincode={pin}` — single braces,
-- so nothing substituted and the literal string reached the API, which
-- answered "No dealer details found for the given pincode".
--
-- Every stored field looked correct. The arguments were right (`pin: 124001`),
-- the status was a plausible 404, and the same request from Postman worked.
-- The fault was only visible in the URL that was actually sent, and that was
-- the one thing not recorded.
--
-- With this, arguments + url are enough to replay any invocation through the
-- console's test button, which is why the response body is still not stored:
-- a client API answers with customer records, and a failure can be reproduced
-- from these two fields without keeping any of them.
--
-- Nullable, and stays NULL for everything recorded before today.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

ALTER TABLE tool_invocations ADD COLUMN IF NOT EXISTS url TEXT;

COMMIT;
