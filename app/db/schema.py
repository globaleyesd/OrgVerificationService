"""Database schema. Rendered from config so the vector size always matches the embedding model."""


def render_schema(embedding_dimensions: int) -> str:
    return f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    id               SERIAL PRIMARY KEY,
    username         TEXT UNIQUE NOT NULL,
    password_hash    TEXT NOT NULL,
    clearance_level  TEXT NOT NULL,
    role             TEXT NOT NULL DEFAULT 'consumer',   -- consumer | provider (decides which UI mode the user gets)
    failed_logins    INTEGER NOT NULL DEFAULT 0,
    locked_until     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id               SERIAL PRIMARY KEY,
    title            TEXT NOT NULL,
    file_type        TEXT NOT NULL,
    storage_path     TEXT NOT NULL,
    clearance_level  TEXT NOT NULL,
    uploaded_by      INTEGER REFERENCES users(id),
    version          INTEGER NOT NULL DEFAULT 1,
    status           TEXT NOT NULL DEFAULT 'pending',   -- pending | ready | failed
    size_bytes       BIGINT NOT NULL DEFAULT 0,          -- used by the cost calculator
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Clearance is stored on every chunk so retrieval can filter in a single query.
CREATE TABLE IF NOT EXISTS chunks (
    id               BIGSERIAL PRIMARY KEY,
    document_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    text             TEXT NOT NULL,
    location         JSONB NOT NULL,      -- e.g. {{"type":"pdf","page":3,"bbox":[..]}} or {{"type":"csv","row":7,"col":"B"}}
    clearance_level  TEXT NOT NULL,
    embedding        vector({int(embedding_dimensions)}) NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_clearance_idx ON chunks (clearance_level);
CREATE INDEX IF NOT EXISTS chunks_text_fts_idx ON chunks USING gin (to_tsvector('english', text));   -- keyword search

CREATE TABLE IF NOT EXISTS facts (
    id               BIGSERIAL PRIMARY KEY,
    entity           TEXT NOT NULL,
    value            TEXT NOT NULL,
    fact_date        DATE,
    source_chunk_ids BIGINT[] NOT NULL,
    clearance_level  TEXT NOT NULL,       -- highest level among the sources
    status           TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Simple job queue so no separate queue service is needed (COST).
CREATE TABLE IF NOT EXISTS jobs (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,            -- ingest_document | extract_facts
    payload     JSONB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',         -- queued | running | done | failed
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    user_id       INTEGER REFERENCES users(id),
    at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    query         TEXT NOT NULL,
    chunk_ids     BIGINT[] NOT NULL DEFAULT '{{}}',
    withheld      INTEGER NOT NULL DEFAULT 0
);

-- AI usage per call. The daily token cap sums this table; the cost calculator prices it.
CREATE TABLE IF NOT EXISTS llm_usage (
    id             BIGSERIAL PRIMARY KEY,
    at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    model          TEXT NOT NULL,
    input_tokens   BIGINT NOT NULL,
    output_tokens  BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS llm_usage_at_idx ON llm_usage (at);

-- One row per heartbeat while the server runs; the cost calculator counts them to measure compute hours.
CREATE TABLE IF NOT EXISTS usage_heartbeat (
    at TIMESTAMPTZ PRIMARY KEY DEFAULT now()
);
"""
