# 2026-03-10 — Initial Architecture Decisions

## Context

First architecture review of ARCHITECTURE.md v0.1. Reviewed by jasper-code.

## Decisions Made

### D-ARCH-3 revised: Two-Phase Contradiction Detection
**Problem:** Original design claimed synchronous contradiction check was a guarantee, but acknowledged a race window in its own text. Self-contradictory.
**Decision:** Renamed to Two-Phase Contradiction Detection. Pre-commit check catches common case (new fact vs. established knowledge). Post-commit ContradictionSweeper catches concurrent writes. Both are necessary. Neither alone sufficient.

### Contradiction threshold: Global in Phase 1, tag-based in Phase 2
**Problem:** Global cosine similarity threshold (0.75) is naive across domains with different semantic densities.
**Decision:** Accept global threshold in Phase 1 (conflicts are only flagged, not auto-resolved). Document as known limitation. Plan tag-based thresholds for Phase 2 once we have operational data to tune with.

### Transport error handling contract
**Problem:** InProcessTransport had no error isolation. A failing agent callback could block propagation to all other agents.
**Decision:** All Transport implementations must: catch callback exceptions, log to ops, continue to next agent, leave failed events as undelivered. 5s timeout on callbacks.

### Agent registration: Implicit via connect()
**Problem:** Agent registration lifecycle was undefined. No clarity on when/how agent rows are created.
**Decision:** Upsert on connect(). First call creates agent with defaults. Subsequent calls update last_seen_at. Config is source of truth for subscriptions.

### Resolved Q1-Q4
- **Connection pooling:** Shared asyncpg pool via config
- **Embedding caching:** LRU wrapper (1024 entries)
- **Migration order:** Supabase shared_learnings first
- **Test database:** Homebrew Postgres local, Docker Compose for CI later

## Local Dev Environment
- Homebrew Postgres 16 + pgvector (already installed, needs PATH linking)
- Docker Compose deferred — easy to add later since all DB interaction goes through asyncpg connection string
