-- Extraction state tracking for incremental daily-notes pipeline

CREATE SCHEMA IF NOT EXISTS mycelium;

CREATE TABLE IF NOT EXISTS mycelium.extraction_state (
    workspace_key TEXT PRIMARY KEY,
    last_file_path TEXT,
    last_line_offset INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_extraction_state_updated_at
    ON mycelium.extraction_state(updated_at);

INSERT INTO mycelium.schema_migrations (version, name)
VALUES (2, '002_extraction_state')
ON CONFLICT (version) DO NOTHING;
