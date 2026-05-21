-- Studio databases — keep LiteLLM and Langfuse out of the app DB so their
-- migrations can never clobber our tables.
CREATE DATABASE langfuse;
CREATE DATABASE litellm;

\connect studio

-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Knowledge base for cross-sprint learning
CREATE TABLE IF NOT EXISTS kb_entries (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sprint_id   TEXT,
    agent_role  TEXT NOT NULL,
    kind        TEXT NOT NULL,           -- 'market_signal' | 'competitor' | 'lesson' | 'insight'
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    embedding   vector(1536),
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS kb_entries_embedding_idx
    ON kb_entries USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS kb_entries_kind_idx ON kb_entries(kind);
CREATE INDEX IF NOT EXISTS kb_entries_sprint_idx ON kb_entries(sprint_id);

-- Cost ledger: every LLM call recorded for budget enforcement
CREATE TABLE IF NOT EXISTS cost_ledger (
    id              BIGSERIAL PRIMARY KEY,
    sprint_id       TEXT,
    agent_role      TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_tokens   INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cached_tokens   INTEGER DEFAULT 0,
    cost_usd        NUMERIC(10, 6) NOT NULL,
    occurred_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cost_ledger_occurred_idx ON cost_ledger(occurred_at);
CREATE INDEX IF NOT EXISTS cost_ledger_sprint_idx ON cost_ledger(sprint_id);

-- Sprint registry (high-level metadata; full state lives in LangGraph checkpoints)
CREATE TABLE IF NOT EXISTS sprints (
    id           TEXT PRIMARY KEY,
    niche_hint   TEXT,
    status       TEXT NOT NULL DEFAULT 'planning',
    decision     TEXT,                    -- 'approved' | 'rejected' | NULL
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    finished_at  TIMESTAMPTZ
);
