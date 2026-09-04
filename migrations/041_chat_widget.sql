-- The agent on a website, not just on the phone.
--
-- The chat engine already exists and is proven by the console tester. This is
-- the second door into it: a public endpoint and a script tag.
--
--
-- THE KEY IN THE SCRIPT TAG IS NOT A SECRET
--
-- It sits in the page source of a public website. Anyone can read it and
-- anyone can call the endpoint with it. That is not a flaw to be fixed with a
-- longer key - it is the shape of the thing, and it decides everything else:
--
--   allowed_origins   the browser sends Origin and it cannot be forged BY a
--                     browser. Empty means the widget is off. Fail closed,
--                     because the failure mode of fail-open is a stranger's
--                     site running your bot on your bill.
--   daily_token_cap   the ceiling on what one day can cost. In TOKENS and not
--                     rupees on purpose: a rupee cap needs a rate table to be
--                     complete, the dashboard currently says five providers
--                     have no rate, and a cap that silently fails because
--                     somebody did not fill in a price is not a cap.
--
-- Neither stops a determined person with a browser. Together they stop the
-- ordinary ways this goes wrong: a copied snippet, a scraper, a loop.

CREATE TABLE IF NOT EXISTS chat_widgets (
    id           BIGSERIAL PRIMARY KEY,
    campaign_id  BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    tenant_id    BIGINT,

    -- Public. Named so, so nobody stores it like a secret and nobody panics
    -- when they find it in a page.
    public_key   TEXT NOT NULL UNIQUE,

    -- Exact origins, "https://www.example.com". No wildcards: a wildcard is
    -- how one hostname becomes every subdomain somebody else can register.
    allowed_origins TEXT[] NOT NULL DEFAULT '{}',

    enabled      BOOLEAN NOT NULL DEFAULT true,
    daily_token_cap BIGINT NOT NULL DEFAULT 500000,

    -- What the visitor sees before they type anything. Separate from the
    -- campaign's spoken greeting: "Namaste, main Hero MotoCorp se bol rahi
    -- hoon" is written to be heard on a phone.
    welcome      TEXT,
    title        TEXT,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (campaign_id)
);

-- Widget conversations ARE stored, unlike the console tester. A tester is a
-- scratchpad; this is a real customer saying real things, and the same reasons
-- calls are kept apply.
CREATE TABLE IF NOT EXISTS chat_conversations (
    id           BIGSERIAL PRIMARY KEY,
    widget_id    BIGINT REFERENCES chat_widgets(id) ON DELETE CASCADE,
    campaign_id  BIGINT,
    tenant_id    BIGINT,
    -- Held by the browser and sent back on every turn. Not a login: it only
    -- ties one visitor's messages together.
    session_id   TEXT NOT NULL UNIQUE,
    origin       TEXT,
    -- Set when the bot has taken a name and a number, so the callback list can
    -- find it without reading transcripts.
    handoff_name  TEXT,
    handoff_phone TEXT,
    prompt_tokens     BIGINT NOT NULL DEFAULT 0,
    completion_tokens BIGINT NOT NULL DEFAULT 0,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    -- What the agent did to produce this: documents retrieved with scores,
    -- tools called. The same working the console tester shows, kept so a
    -- complaint about a wrong answer can be answered.
    steps           JSONB,
    ms              INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_conv_idx
    ON chat_messages (conversation_id, id);
CREATE INDEX IF NOT EXISTS chat_conversations_campaign_idx
    ON chat_conversations (campaign_id, started_at DESC);

-- The cap is asked on every turn, so it has to be one indexed sum.
CREATE INDEX IF NOT EXISTS chat_conversations_widget_day_idx
    ON chat_conversations (widget_id, last_at);

SELECT count(*) AS widgets FROM chat_widgets;
