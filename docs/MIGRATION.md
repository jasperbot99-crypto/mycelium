# Migration Guide

This guide covers cutover migration into Mycelium from legacy memory stores.

## Principles

- Migration is cutover-based, not dual-write.
- Imported facts start with confidence `0.7`.
- Imported facts include traceability metadata:
  - `metadata.migrated_from`
  - `metadata.migration_date`
  - `metadata.original_id` (when available)
- Legacy embeddings are discarded and regenerated during ingest.

## 1) Apply Schema

```bash
mycelium-migrate apply-schema \
  --database-url "postgresql://localhost:5432/mycelium_dev" \
  --file migrations/001_initial.sql
```

## 2) Dry Run (No Writes)

Use dry-run before cutover to validate extraction volumes:

```bash
mycelium-migrate run \
  --database-url "postgresql://localhost:5432/mycelium_dev" \
  --source lancedb \
  --lancedb-json /path/to/lancedb_export.json \
  --dry-run
```

## 3) Source Imports

### Supabase `shared_learnings`

```bash
mycelium-migrate run \
  --database-url "postgresql://localhost:5432/mycelium_dev" \
  --source supabase \
  --supabase-database-url "postgresql://<supabase-conn>" \
  --supabase-query "SELECT id, agent_id, subject, predicate, content, context, tags, created_at FROM shared_learnings ORDER BY created_at ASC"
```

### LanceDB Export (JSON)

```bash
mycelium-migrate run \
  --database-url "postgresql://localhost:5432/mycelium_dev" \
  --source lancedb \
  --lancedb-json /path/to/lancedb_export.json
```

### Memory Files (LLM-assisted)

```bash
mycelium-migrate run \
  --database-url "postgresql://localhost:5432/mycelium_dev" \
  --source memory_file \
  --memory-file /path/to/MEMORY.md \
  --memory-file /path/to/SOUL.md
```

`OPENAI_API_KEY` must be set (or pass `--openai-api-key`).

### Combined Ordered Run

Order is always:
1. Supabase
2. LanceDB
3. Memory files

```bash
mycelium-migrate run \
  --database-url "postgresql://localhost:5432/mycelium_dev" \
  --source all \
  --supabase-database-url "postgresql://<supabase-conn>" \
  --lancedb-json /path/to/lancedb_export.json \
  --memory-file /path/to/MEMORY.md \
  --stop-on-error
```

## OAuth / Token Refresh Support

If static API keys are not suitable, inject a custom `TokenProvider` for:

- `OpenAIEmbeddingProvider`
- `OpenAIConflictResolver` / `AnthropicConflictResolver`
- `OpenAIMemoryFactExtractor`

Each request fetches token from the provider, so OAuth refresh logic can run per call.

## Rollback Strategy

Mycelium facts are immutable and append-first. Recommended rollback strategy is operational:

- Run `--dry-run` first.
- Migrate in source order.
- Use `--stop-on-error` to halt on first problematic source.
- If you need full rollback guarantees, run migration against a fresh database and swap connection at cutover.
