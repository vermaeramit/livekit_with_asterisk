-- What actually went out on the wire, and why an error came back.
--
-- `url` was added in migration 014 after a placeholder written with single
-- braces sent `{pin}` verbatim: the arguments looked perfectly correct and only
-- the resolved url showed the fault. A POST body has exactly the same blind
-- spot, and it bit on call 365 - two tools returned 400 because their templates
-- did not produce valid JSON, and nothing recorded showed it:
--
--   price          - a missing comma after "city"
--   exchange_price - "month": {{month}} filled with 04, and JSON numbers may
--                    not have a leading zero
--
-- Both invisible from arguments and url alone.

ALTER TABLE tool_invocations
    ADD COLUMN IF NOT EXISTS request text;

COMMENT ON COLUMN tool_invocations.request IS
    'The resolved request body actually sent, after placeholder substitution. NULL for GET.';
