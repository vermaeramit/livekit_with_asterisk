CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============ agent configuration (admin UI will CRUD this) ============
CREATE TABLE agent_config (
    id                SERIAL PRIMARY KEY,
    name              TEXT UNIQUE NOT NULL,
    enabled           BOOLEAN     NOT NULL DEFAULT true,

    language          TEXT        NOT NULL DEFAULT 'hi-IN',
    greeting          TEXT,
    instructions      TEXT        NOT NULL,

    stt_provider      TEXT        NOT NULL DEFAULT 'google',
    stt_model         TEXT,
    llm_provider      TEXT        NOT NULL DEFAULT 'google',
    llm_model         TEXT        NOT NULL DEFAULT 'gemini-flash-latest',
    llm_temperature   REAL        NOT NULL DEFAULT 0.6,
    tts_provider      TEXT        NOT NULL DEFAULT 'google',
    tts_model         TEXT,
    tts_voice         TEXT,

    allow_interrupt   BOOLEAN     NOT NULL DEFAULT true,
    max_turns         INT         NOT NULL DEFAULT 40,
    max_duration_sec  INT         NOT NULL DEFAULT 600,

    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============ per-call record ============
CREATE TABLE calls (
    id             BIGSERIAL PRIMARY KEY,
    room_name      TEXT NOT NULL,
    sip_call_id    TEXT,
    caller         TEXT,
    callee         TEXT,
    config_name    TEXT,
    language       TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at       TIMESTAMPTZ,
    duration_ms    INT,
    end_reason     TEXT,
    outcome        TEXT,
    recording_path TEXT
);
CREATE INDEX ON calls (started_at DESC);
CREATE INDEX ON calls (room_name);

-- ============ transcript + per-stage latency ============
CREATE TABLE turns (
    id          BIGSERIAL PRIMARY KEY,
    call_id     BIGINT REFERENCES calls(id) ON DELETE CASCADE,
    seq         INT  NOT NULL,
    role        TEXT NOT NULL,           -- 'user' | 'agent'
    text        TEXT,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- instrumentation: real numbers, not estimates
    eou_ms      INT,      -- end-of-utterance detection
    stt_ms      INT,
    llm_ttft_ms INT,
    tts_ttfb_ms INT,
    total_ms    INT,
    interrupted BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX ON turns (call_id, seq);

-- ============ default config ============
INSERT INTO agent_config (name, language, greeting, instructions, tts_voice) VALUES (
  'default',
  'hi-IN',
  'Namaste! Main aapki kaise madad kar sakta hoon?',
  'You are a helpful voice assistant on a phone call. '
  'Keep replies short - one or two sentences. This is speech, not text. '
  'Never use bullet points, markdown, or emoji. '
  'Reply in the same language and script the caller uses. '
  'If you do not know something, say so plainly instead of guessing.',
  NULL
);
