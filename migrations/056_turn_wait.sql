-- How long the caller actually waited for each answer, and what filled the wait.
--
-- The call page's "total" was eou_ms + llm_ttft_ms + tts_ttfb_ms. On a turn that
-- searched the knowledge base that left out the search itself, the second model
-- call that read its results, and every filler the answer queued behind. Call
-- 619, 22 Sep 2026: one turn read 1.97 s on the page; the caller stopped at
-- 09:48:52.44 and the answer began at 09:48:59.72.
--
-- total_ms is kept exactly as it was. It is a sum of provider delays and is
-- still the right thing to split a slow turn by - it was only ever wrong as a
-- measure of the wait. Redefining it would also have silently changed the
-- meaning of every figure already stored.

ALTER TABLE turns
    -- livekit's own e2e_latency for this answer: from the caller's last word to
    -- the first audio of THIS speech. Carried across a tool call by livekit
    -- itself when the first reply said nothing. NULL for turns that are not an
    -- answer to the caller - the greeting, a silence prompt - and for every
    -- turn written before this column existed.
    ADD COLUMN IF NOT EXISTS wait_ms INTEGER,

    -- What happened inside that wait, relative to the caller's last word:
    --   [{"kind": "filler", "label": "जी…",                  "at_ms": 1210, "ms": 1040},
    --    {"kind": "lookup", "label": "knowledge base search", "at_ms": 2350, "ms": 3086}]
    -- Fillers are what the caller heard; lookups are what the agent was doing.
    -- A stretch covered by neither is silence while the model was still working.
    ADD COLUMN IF NOT EXISTS timeline JSONB;

COMMENT ON COLUMN turns.wait_ms IS
    'Caller''s last word to the first audio of this answer (livekit e2e_latency). '
    'Includes lookups, the second model call after a tool, and any filler played '
    'first. total_ms does not, and is kept as the provider split.';
