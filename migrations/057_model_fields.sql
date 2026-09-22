-- Which dialler fields were actually given to the model, per call.
--
-- Until now the three "prompt-safe" fields - caller name, product they own,
-- call type - went to the model on every call as a separate CALLER CONTEXT
-- message, whether or not the campaign's prompt had any use for them, and the
-- console could not show that message anywhere. A campaign owner looking at a
-- prompt that never mentions a name had no way to know the agent was being told
-- one - and told to greet the caller by it.
--
-- From here a field reaches the model only when the campaign's prompt uses it:
-- {{cus_name}}, {{modalname}}, {{calltype}}. What was given on each call is
-- recorded here so the call page says what actually happened on THAT call,
-- rather than what the rule is today.
--
-- NULL on every call before this migration. On those, all three were given
-- whenever the dialler sent them, and the console says so.

ALTER TABLE calls ADD COLUMN IF NOT EXISTS model_fields TEXT[];

COMMENT ON COLUMN calls.model_fields IS
    'dialer.* keys whose values were given to the model on this call - the '
    'prompt-safe fields the campaign prompt referenced as {{placeholders}}. '
    'Empty when the prompt referenced none; NULL on calls before migration 057, '
    'when all three were always given.';
