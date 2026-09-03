-- A knowledge base that comes from a URL.
--
-- The knowledge bank this was built for is an Excel workbook published as
-- HTML: 47 sheets behind one address, updated centrally and often. Uploading
-- it as a file means somebody re-exporting and re-uploading every time a price
-- changes, which is a job nobody does for long.
--
-- ONE DOCUMENT PER PAGE, NOT ONE PER SOURCE
--
-- Each sheet becomes an ordinary kb_document. Everything that already works
-- per document then works per sheet: enable/disable, the chunk viewer, and
-- citations that name where an answer came from.
--
-- That matters more than it sounds. Of the 47 sheets, one is a list of 2059
-- corporate vendor accounts and another is a dealer directory - real data, and
-- nothing a caller asking about a motorcycle should ever be answered from. As
-- separate documents they can be switched off. As one blob they could not.

CREATE TABLE IF NOT EXISTS kb_sources (
    id          BIGSERIAL PRIMARY KEY,
    campaign_id BIGINT REFERENCES campaigns(id) ON DELETE CASCADE,
    tenant_id   BIGINT,
    config_name TEXT   NOT NULL,
    url         TEXT   NOT NULL,
    title       TEXT,

    -- Refresh is a button somebody presses, chosen deliberately over a nightly
    -- fetch: the alternative is the agent quoting a new price nobody has read.
    -- The cost is that this can go stale silently, so the console shows the age
    -- of last_fetched_at rather than just the date.
    last_fetched_at TIMESTAMPTZ,
    last_status     TEXT,
    last_error      TEXT,
    page_count      INTEGER NOT NULL DEFAULT 0,
    -- Pages that held no readable text. Kept as a list, not a count: "New
    -- Prices Oil & Consummables is a screenshot" is the useful sentence, and
    -- without it nobody learns why the agent does not know oil prices.
    skipped         JSONB   NOT NULL DEFAULT '[]'::jsonb,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, url)
);

ALTER TABLE kb_documents
    ADD COLUMN IF NOT EXISTS source_id  BIGINT
        REFERENCES kb_sources(id) ON DELETE CASCADE,
    -- The page this came from. Better than a filename in a citation: it says
    -- where to go and check.
    ADD COLUMN IF NOT EXISTS source_url TEXT;

CREATE INDEX IF NOT EXISTS kb_documents_source_idx
    ON kb_documents (source_id) WHERE source_id IS NOT NULL;

COMMENT ON COLUMN kb_documents.source_id IS
    'NULL for an uploaded file. Set for a page pulled from a kb_source, and '
    'ON DELETE CASCADE so removing the source removes everything it produced.';

SELECT count(*) AS sources FROM kb_sources;
SELECT count(*) AS documents,
       count(*) FILTER (WHERE source_id IS NOT NULL) AS from_a_url
  FROM kb_documents;
