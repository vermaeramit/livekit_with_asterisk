-- Let the payload be just the answers.
--
-- The envelope splits call / dialer / extracted / tools because those have
-- different levels of trust and a consumer needs to know which is which. That
-- is right when the consumer is ours. It is wrong when the consumer is the
-- client's existing endpoint, which usually wants a flat object of the fields
-- it asked for and nothing else.
--
-- Defaults to true, so no campaign already delivering changes shape on the
-- morning of a migration. Turn it off per campaign in the console.

ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS postback_full_payload boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN agent_config.postback_full_payload IS
    'true = call/dialer/extracted/tools envelope. false = the extracted fields alone, flat.';
