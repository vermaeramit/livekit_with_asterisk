-- Something to say while the knowledge base is being searched.
--
-- Campaign tools have had this since migration 018. search_knowledge_base never
-- did, because it is built in rather than configured - and it was the rarer
-- path then. With 26 documents and 108k tokens it is now the common one: five
-- searches in five turns on the first real call, each costing 810-1860 ms of
-- silence with the caller waiting.
--
-- NULL or empty = say nothing, which is today's behaviour.

ALTER TABLE agent_config
    ADD COLUMN IF NOT EXISTS kb_filler_message text;

COMMENT ON COLUMN agent_config.kb_filler_message IS
    'Spoken if search_knowledge_base is still running after TOOL_FILLER_AFTER_MS. NULL = silence.';
