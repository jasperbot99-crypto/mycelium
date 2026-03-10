# 2026-03-10 — Phase 2 Deferred Items Completed

## Context

Two deferred items remained open in Phase 2:

1. Supabase Realtime transport (cross-process push)
2. Memory file extractor (LLM-assisted migration source)

## Decision

### 1) Supabase Realtime transport

Implemented `SupabaseRealtimeTransport` in `src/mycelium/transport/supabase_rt.py` using
PostgreSQL `LISTEN/NOTIFY` channels on the Supabase-hosted Postgres instance.

- Channel naming follows per-agent routing: `mycelium_agent_<agent_id>`
- `publish()` sends JSON payloads via `pg_notify`
- `subscribe()` registers async callbacks and dispatches with timeout protection
- Errors and timeouts are isolated per callback, matching transport error contract

Rationale:
- Cross-process push is achieved without adding new dependencies.
- It remains compatible with the existing replay guarantee through `propagation_events`.
- This keeps the transport swappable and aligned with the architecture boundary.

### 2) Memory file extractor

Implemented LLM-assisted extraction in `src/mycelium/migration/memory_file_extractor.py`:

- `MemoryFactExtractor` protocol
- `OpenAIMemoryFactExtractor` (HTTP + JSON extraction contract)
- `extract_from_memory_file(s)` helpers
- `import_memory_file_records()` ingestion path with migration metadata

All imported records follow the migration rules:
- `source_type = agent_extraction`
- `initial_confidence = 0.7`
- metadata includes `migrated_from = "memory_file"` and `migration_date`

## Consequences

- Phase 2 deferred items are now implemented and covered by unit tests.
- Client now auto-selects Supabase realtime transport when config provides Supabase credentials.
- Future transport swap (e.g., direct Supabase Realtime WS protocol) remains possible behind the transport interface.
