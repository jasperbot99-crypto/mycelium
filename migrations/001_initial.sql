-- Mycelium initial schema — ARCHITECTURE.md Section 3
-- Requires: PostgreSQL 16+, pgvector extension

-- Extensions
CREATE EXTENSION IF NOT EXISTS vector;

-- Schemas
CREATE SCHEMA IF NOT EXISTS mycelium;
CREATE SCHEMA IF NOT EXISTS ops;

-- 3.2 Agents (must be created before facts due to FK)
CREATE TABLE mycelium.agents (
    id              TEXT PRIMARY KEY,
    role            TEXT NOT NULL,
    trust_score     FLOAT NOT NULL DEFAULT 0.5,
    facts_contributed   INT NOT NULL DEFAULT 0,
    facts_contradicted  INT NOT NULL DEFAULT 0,
    contradiction_rate  FLOAT NOT NULL DEFAULT 0.0,

    -- Active context (6.2.1)
    active_task     TEXT,
    active_entities TEXT[],
    active_urgency  TEXT CHECK (active_urgency IN ('normal', 'elevated', 'critical'))
                        DEFAULT 'normal',
    context_updated_at TIMESTAMPTZ,

    -- Lifecycle
    last_seen_at    TIMESTAMPTZ,
    last_ack_event_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    metadata        JSONB DEFAULT '{}'
);

-- 3.1 Facts — the core table
CREATE TABLE mycelium.facts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Structured content (6.1.1)
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    predicate_canonical TEXT,
    object          TEXT NOT NULL,
    context         TEXT,

    -- Provenance
    source_agent_id TEXT NOT NULL REFERENCES mycelium.agents(id),
    source_type     TEXT NOT NULL CHECK (source_type IN (
                        'human_correction', 'system_verification',
                        'agent_extraction', 'agent_inference')),

    -- Scoring
    confidence      FLOAT NOT NULL DEFAULT 0.5,
    trust_score     FLOAT NOT NULL DEFAULT 0.5,

    -- Bi-temporal model
    valid_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expired_at      TIMESTAMPTZ,

    -- Provenance chain
    derived_from    UUID[],
    supersedes      UUID,

    -- Quality signals
    corroboration_count INT NOT NULL DEFAULT 0,
    last_accessed_at    TIMESTAMPTZ,
    access_count        INT NOT NULL DEFAULT 0,
    last_verified_at    TIMESTAMPTZ,
    verification_status TEXT CHECK (verification_status IN (
                            'unverified', 'verified', 'failed', 'stale'))
                        DEFAULT 'unverified',

    -- Classification
    tags            TEXT[] NOT NULL DEFAULT '{}',

    -- Conflict status
    conflict_status TEXT CHECK (conflict_status IN (
                        'none', 'unresolved', 'resolved')) DEFAULT 'none',

    -- Embedding (pgvector)
    embedding       vector(1536),

    -- Extensible
    metadata        JSONB DEFAULT '{}',

    -- Migration tracking
    migrated_from   TEXT,
    migration_date  TIMESTAMPTZ
);

CREATE INDEX idx_facts_subject ON mycelium.facts(subject);
CREATE INDEX idx_facts_tags ON mycelium.facts USING GIN(tags);
CREATE INDEX idx_facts_valid ON mycelium.facts(valid_from, valid_until)
    WHERE expired_at IS NULL;
CREATE INDEX idx_facts_source_agent ON mycelium.facts(source_agent_id);
CREATE INDEX idx_facts_conflict ON mycelium.facts(conflict_status)
    WHERE conflict_status = 'unresolved';
CREATE INDEX idx_facts_predicate_canonical ON mycelium.facts(predicate_canonical)
    WHERE predicate_canonical IS NOT NULL;
CREATE INDEX idx_facts_created_at ON mycelium.facts(created_at);

-- NOTE: ivfflat index requires data to exist first. Created separately after initial data load.
-- For development with small data, exact search (no index) is fine.
-- CREATE INDEX idx_facts_embedding ON mycelium.facts
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 3.3 Subscriptions
CREATE TABLE mycelium.subscriptions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT NOT NULL REFERENCES mycelium.agents(id),
    topic           TEXT NOT NULL,
    priority        TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'critical')),
    min_confidence  FLOAT DEFAULT 0.0,
    source_types    TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(agent_id, topic)
);

-- 3.4 Fact relations
CREATE TABLE mycelium.fact_relations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_fact_id  UUID NOT NULL REFERENCES mycelium.facts(id),
    target_fact_id  UUID NOT NULL REFERENCES mycelium.facts(id),
    relation_type   TEXT NOT NULL CHECK (relation_type IN (
                        'contradicts', 'supersedes', 'derived_from',
                        'corroborates', 'depends_on')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT,
    metadata        JSONB DEFAULT '{}',

    UNIQUE(source_fact_id, target_fact_id, relation_type)
);

CREATE INDEX idx_relations_source ON mycelium.fact_relations(source_fact_id);
CREATE INDEX idx_relations_target ON mycelium.fact_relations(target_fact_id);
CREATE INDEX idx_relations_type ON mycelium.fact_relations(relation_type);

-- 3.5 Propagation events
CREATE TABLE mycelium.propagation_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_id         UUID NOT NULL REFERENCES mycelium.facts(id),
    target_agent_id TEXT NOT NULL REFERENCES mycelium.agents(id),
    reason          TEXT NOT NULL,
    priority        TEXT NOT NULL,
    delivered       BOOLEAN NOT NULL DEFAULT FALSE,
    delivered_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    sequence_num    BIGSERIAL
);

CREATE INDEX idx_events_agent_seq ON mycelium.propagation_events(target_agent_id, sequence_num);
CREATE INDEX idx_events_undelivered ON mycelium.propagation_events(target_agent_id)
    WHERE delivered = FALSE;

-- 3.6 Conflicts
CREATE TABLE mycelium.conflicts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_a_id       UUID NOT NULL REFERENCES mycelium.facts(id),
    fact_b_id       UUID NOT NULL REFERENCES mycelium.facts(id),
    status          TEXT NOT NULL CHECK (status IN (
                        'detected', 'auto_resolved', 'llm_resolved',
                        'human_resolved', 'escalated')),
    resolution      JSONB,
    winning_fact_id UUID REFERENCES mycelium.facts(id),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT,

    metadata        JSONB DEFAULT '{}'
);

-- 3.7 Ops log (separate schema)
CREATE TABLE ops.operation_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation       TEXT NOT NULL,
    agent_id        TEXT,
    fact_id         UUID,
    status          TEXT NOT NULL,
    latency_ms      INT,
    detail          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ops_operation ON ops.operation_log(operation, created_at);
CREATE INDEX idx_ops_agent ON ops.operation_log(agent_id, created_at);

-- Migration tracking
CREATE TABLE mycelium.schema_migrations (
    version     INT PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO mycelium.schema_migrations (version, name) VALUES (1, '001_initial');
