-- =============================================================================
-- 015 — Paging the tool activity list
--
-- The console's "Recent activity" panel asks for the newest N invocations of a
-- campaign's tools. The existing indexes do not serve that:
--
--   tool_invocations_call_idx   (call_id, created_at)  — one call at a time
--   tool_invocations_error_idx  (created_at DESC) WHERE error IS NOT NULL
--
-- Neither covers "newest first across every call", which is what a paged list
-- does on every page turn. Without it the table is scanned and sorted, and
-- tool_invocations grows with every call that uses a tool.
--
-- Plain created_at rather than a composite with campaign_id, because
-- tool_invocations has no campaign_id — it reaches one through calls. Adding a
-- denormalised copy would be the next step if a single deployment ever runs
-- enough campaigns for the join to dominate; today it does not, and an unused
-- column that must be kept in sync is its own liability.
--
-- Safe to re-run.
-- =============================================================================

BEGIN;

CREATE INDEX IF NOT EXISTS tool_invocations_recent_idx
    ON tool_invocations (created_at DESC, id DESC);

COMMIT;
