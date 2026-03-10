# Decision: In-Memory Storage Before Postgres

_Date: 2026-03-10_
_Status: Accepted_

## Context

With domain types, protocols, trust scoring, conflict detection, and transport all built and tested (81 tests), the next step was either:

1. Postgres schema + asyncpg implementations, then pipelines
2. In-memory storage implementations, then pipelines, then Postgres as a swap

## Decision

**Option 2: In-memory first.**

## Rationale

- **Protocols are already defined.** Simple dict-based implementations satisfy the same interfaces, making them trivially correct.
- **Pipelines are where design issues surface.** Building IngestPipeline and QueryEngine against in-memory repos validates that our protocol surface area is correct *before* writing SQL. Changing a protocol method signature is cheap; changing SQL + Python in parallel is not.
- **Testing speed.** All pipeline tests run as unit tests (~0.2s for 132 tests). No DB setup, no teardown, no port conflicts. Integration tests with Postgres come later as a verification layer, not the primary feedback loop.
- **Postgres becomes a pure swap.** When we implement asyncpg repos, we're implementing the exact same protocol — no surprises about missing methods or wrong return types.

## What Was Built

1. `src/mycelium/storage/memory.py` — 6 in-memory implementations:
   - InMemoryFactRepository, InMemoryAgentRepository, InMemorySubscriptionRepository
   - InMemoryConflictRepository, InMemoryRelationRepository, InMemoryEventLog
2. `src/mycelium/embeddings/mock.py` — MockEmbeddingProvider (deterministic, hash-based)
3. `src/mycelium/pipelines/ingest.py` — IngestPipeline (validate → resolve → embed → conflict check → score → store)
4. `src/mycelium/pipelines/query.py` — QueryEngine (embed → retrieve → filter → rank) + QueryFilters, QueryResult
5. `src/mycelium/client/client.py` — MyceliumClient facade (connect, ingest, query, correct, update_context, replay)

## Test Coverage

- 51 new tests (28 storage, 15 pipeline, 8 client end-to-end)
- Total: 132 tests, all passing

## Next Steps

- Postgres schema (001_initial.sql) + asyncpg implementations
- Swap in-memory repos for Postgres repos in integration tests
- In-memory repos remain permanently useful for unit tests
