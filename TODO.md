# Mycelium — TODO

_Updated: 2026-03-11_

## Phase 1: Core Memory Layer

### Setup
- [x] Project scaffolding (pyproject.toml, src layout, test structure)
- [x] Local Postgres 16 + pgvector 0.8.2 setup and verification
- [x] Database migration: 001_initial.sql (all schemas, tables, indexes)
- [ ] CI-ready Docker Compose (deferred — Homebrew for now)

### Domain Types (ARCHITECTURE.md Section 5)
- [x] Core types: Fact, FactContent, ActiveContext, Subscription, PropagationEvent, Conflict, RejectionReason
- [x] Enums: SourceType, Priority, Urgency, ConflictStatus, RelationType, VerificationStatus
- [x] Predicate resolver: canonical alias table + lookup

### Storage Layer (ARCHITECTURE.md Section 3)
- [x] Storage protocols (abstract interfaces)
- [x] In-memory implementations (all 6 repos — for unit testing)
- [x] PostgresFactRepository — full CRUD, semantic search, temporal queries
- [x] PostgresAgentRepository — upsert on connect, trust stats
- [x] PostgresConflictRepository — insert, find, resolution
- [x] PostgresRelationRepository — insert, find by fact/type
- [x] PostgresSubscriptionRepository — sync, get for agent/all
- [x] PostgresEventLog — append with BIGSERIAL sequence, delivery tracking
### Embedding (ARCHITECTURE.md Section 6)
- [x] EmbeddingProvider protocol
- [x] MockEmbeddingProvider (deterministic, for testing)
- [x] OpenAI text-embedding-3-small implementation (httpx-based, no SDK dep)
- [x] LRU cache wrapper (CachedEmbeddingProvider, OrderedDict-based)

### Pipelines (ARCHITECTURE.md Section 4)
- [x] IngestPipeline — validate → resolve predicate → embed → contradiction check → score → store
- [x] QueryEngine — embed → retrieve → filter → rank (with QueryFilters, RankingWeights)
- [x] CorrectionPipeline — via MyceliumClient.correct() (expire + ingest superseding fact)
- [x] ContradictionSweeper — post-commit background sweep with start/stop lifecycle

### Trust & Conflict (ARCHITECTURE.md Sections 4.4, Domain)
- [x] TrustScorer — source-type hierarchy, initial confidence, agent history, corroboration
- [x] ConflictDetector — embedding similarity + canonical predicate matching
- [x] Conflict record creation (no resolution in Phase 1)

### Client SDK (ARCHITECTURE.md Section 2, Layer 4)
- [x] MyceliumClient — ingest(), query(), correct(), connect(), disconnect()
- [x] Agent registration lifecycle (upsert on connect)
- [x] Config management (MyceliumConfig)
- [x] update_context(), on_fact(), replay()

### Transport (ARCHITECTURE.md Section 7)
- [x] Transport protocol
- [x] InProcessTransport with error handling and timeout

### Testing
- [x] Test fixtures (conftest.py for unit + integration)
- [x] Unit tests for domain types and logic (20 tests)
- [x] Unit tests for storage layer (28 tests)
- [x] Unit tests for pipelines (15 tests)
- [x] Unit tests for client end-to-end (8 tests)
- [x] Unit tests for transport (6 tests)
- [x] Unit tests for trust (11 tests)
- [x] Unit tests for conflict detection (15 tests)
- [x] Unit tests for predicates (15 tests)
- [x] Integration tests for all Postgres repos (21 tests)
- [x] Scenario runner skeleton + first YAML scenario
- [x] Unit tests for embeddings (18 tests: OpenAI provider + cache)
- [x] Unit tests for ContradictionSweeper (10 tests)
- [x] Unit tests for OpsLogger (13 tests)
- [x] Unit tests for migration importers (14 tests)

**Phase 1 test totals: 193 passed, 0 failed**

---

## Phase 1b: Pre-Phase-2 Cleanup
_Deferred from Phase 1 core — must complete before Phase 2._

- [x] Ops logger — OpsLogger protocol, InMemoryOpsLogger, NullOpsLogger
- [x] Ops logger hooked into IngestPipeline, QueryEngine, MyceliumClient
- [x] Legacy migration base types (MigrationRecord, MigrationResult, MigrationSource)
- [x] Supabase shared_learnings importer (extract + import)
- [x] LanceDB importer (extract + import)
- [x] Migration runner skeleton (FullMigrationResult)
- [x] Memory file extractor (requires LLM — deferred to Phase 2)

## Phase 2: Propagation & Subscriptions

### Propagation Engine (ARCHITECTURE.md Section 7)
- [x] PropagationEngine — evaluate subscriptions, create events, publish via Transport
- [x] Hook propagation into IngestPipeline (after store, before ops log)
- [x] Delivery tracking: client marks delivered on callback success, not engine
- [x] Error contract: transport failures logged, never block ingest

### Client Integration
- [x] MyceliumClient.connect() creates PropagationEngine when deps available
- [x] Transport subscription on connect (client registers _handle_event)
- [x] Auto-replay undelivered events on connect (when on_fact callback registered before connect)
- [x] replay(deliver=True) for manual replay with callback delivery
- [x] Transport unsubscribe on disconnect()

### Scenario Runner
- [x] assert_propagation action (count, min_events, max_events)
- [x] Agents auto-register on_fact listeners for event tracking
- [x] Scenario actions for verify/corroborate + state assertions (fact/agent)

### Testing
- [x] Unit tests for PropagationEngine (14 tests)
- [x] Unit tests for client propagation integration (5 tests)
- [x] YAML scenario: propagation_and_subscriptions (multi-agent propagation)
- [x] YAML scenario: verification_and_corroboration (cross-agent verification flow)

### Remaining Phase 2 Items
- [x] Active context and dynamic relevance matching
- [x] Supabase Realtime transport (cross-process push)
- [x] Memory file extractor (LLM-assisted, deferred)

**Test totals: 234 passed, 0 failed (0.70s)**

## Phase 3: Verification & Trust

### Core Workflows
- [x] Verification workflow via `MyceliumClient.verify()` + `VerificationPipeline`
- [x] Trust score evolution from verification outcomes (agent trust deltas)
- [x] Explicit corroboration workflow via `MyceliumClient.corroborate()`

### Storage + API Support
- [x] FactRepository.update_verification() in-memory + Postgres
- [x] AgentRepository trust_score_delta support in-memory + Postgres
- [x] Domain types for `VerificationMethod`, `VerificationResult`, `CorroborationResult`

### Decay & Garbage Collection
- [x] DecayCycleRunner pipeline — expire low-confidence, failed-verification, and stale facts
- [x] `find_all_active()` on FactRepository protocol + in-memory + Postgres implementations
- [x] Start/stop background loop (same pattern as ContradictionSweeper)
- [x] Unit tests for DecayCycleRunner (14 tests)

### Hallucination Detection
- [x] `check_hallucination()` — pure-function heuristic checks (no LLM)
- [x] Hooked into IngestPipeline as step 2 (reject or reduce confidence)
- [x] Checks: short content, self-referential, inflated confidence, low-trust high-confidence
- [x] Unit tests for hallucination detection (17 tests)

### Pluggable Verification Providers
- [x] `VerificationProvider` Protocol (method + check)
- [x] `ConfidenceThresholdProvider` — baseline score-based verification
- [x] `TemporalConsistencyProvider` — age and future-date checks
- [x] Unit tests for verification providers (16 tests)

**Phase 3 test totals: 312 passed, 0 failed (0.83s)**

## Phase 4: Advanced Conflict Resolution

### Core Workflows
- [x] ConflictResolutionPipeline with deterministic first-pass resolution
- [x] LLM-assisted conflict resolution interface + confidence-gated escalation
- [x] Escalation path for unresolved ambiguous conflicts
- [x] OpenAI + Anthropic conflict resolver providers (config-driven)

### Causal + Consistency
- [x] Causal provenance tracing pipeline (`trace_provenance`)
- [x] Distributed consistency primitives (version vectors + causal ordering)
- [x] Causal metadata assignment at ingest (`version_vector`, causal timestamp)

### Client + API
- [x] `MyceliumClient.resolve_conflicts()` and `resolve_conflict()`
- [x] `MyceliumClient.trace_provenance()`

## Phase 5: Open Source & Generalization

### Server Mode + API
- [x] FastAPI app factory with startup/shutdown lifecycle (`create_app`)
- [x] Shared Postgres pool + repository wiring in `ServerState`
- [x] Background runners in server mode (ContradictionSweeper + DecayCycleRunner)
- [x] Bearer API key auth for `/v1/*`
- [x] REST endpoints for connect/disconnect, ingest/query/correct/verify/corroborate
- [x] REST endpoints for conflict resolution, provenance, subscriptions/context, replay/ack
- [x] Health/readiness/version endpoints (`/health`, `/ready`, `/version`)

### TypeScript SDK
- [x] `sdk/typescript` package scaffold
- [x] Typed `MyceliumHttpClient` with HTTP parity for Phase 5 endpoints
- [x] Retry + timeout + typed error mapping
- [x] Node + OpenClaw-style examples

### OSS Hardening
- [x] CLI entrypoint: `mycelium-server`
- [x] CI workflow for Python checks/tests + TS SDK typecheck
- [x] Changelog + release notes template

### Docs + Examples
- [x] README updated to working beta with quickstarts
- [x] Server mode deployment/auth docs
- [x] API contract + troubleshooting docs
- [x] End-to-end examples (library, server, conflict-resolution)

### Benchmarks
- [x] Reproducible benchmark runner (`benchmarks/run.py`)
- [x] JSON + Markdown report output
- [x] Baseline comparison input (`benchmarks/baseline.json`)

## Post-Beta Hardening (2026-03-11)

- [x] Migration CLI entrypoint (`mycelium-migrate`) with schema apply + source run modes
- [x] Ordered migration runner with dry-run and fail-fast controls
- [x] Migration guide (`docs/MIGRATION.md`)
- [x] TokenProvider auth abstraction for embedding + LLM + memory-file extraction
- [x] Test gap additions: restart/replay recovery, 3-agent conflict handling, ranking order, trust evolution
- [x] UTC datetime normalization across pipelines/storage/tests (naive vs aware crash fix)
- [x] End-to-end integration test: plugin HTTP (`/ingest/raw`) -> server -> Postgres query
- [x] Ops baseline artifacts: nightly extraction launchd template, healthcheck monitor, log rotation scripts

## Improvement Strategy — Phase 0 Foundations (2026-03-11)

- [x] Daily notes extraction pipeline scaffold (`src/mycelium/extraction/daily_notes.py`)
- [x] Incremental extraction watermark storage (`mycelium.extraction_state`)
- [x] Manual extraction trigger endpoint (`POST /v1/extraction/run`)
- [x] Nightly extraction script (`ops/nightly-extraction.sh`)
- [x] Expire legacy `memory_file` migration facts as part of extraction run

## Improvement Strategy — Follow-up Progress (2026-03-11)

- [x] Query ranking uses verification signal and unresolved conflict penalty
- [x] Subject-clustered query consolidation (default one top fact per subject)
- [x] Agent context boost is applied in query ranking
- [x] Fact listing endpoint (`GET /v1/agents/{agent_id}/facts`) with pagination
- [x] Semantic subscription fallback in propagation (`>= 0.6` similarity)
- [x] Real-time correction bridge hardened in connector (explicit correction formats, no dual ingest)
- [x] Agent-aware ranking profiles (role-specific recency/trust/similarity balance)
- [x] Access-weighted stale penalty for unread old facts in query ranking
- [x] Propagation priority escalation + normalized entity matching (`EUR/USD` ↔ `EURUSD`)
- [x] Fact-type TTL decay policy (trading 4h, service status 24h, architecture persistent)
- [x] Ingest temporal supersede-vs-contradict handling (same subject/predicate updates)
- [x] Explicit feedback API (`POST /v1/agents/{agent_id}/feedback`) with fact score/trust updates
- [x] VerificationCycleRunner (automated verification of unverified facts)
- [x] Semantic dedup at ingest (`>0.95` + same subject/predicate/object => no new fact)
- [x] Connect handshake includes `connected_since` for connector-side 1h connect caching
- [x] Server `/metrics` endpoint (fact/agent/conflict counts + query error/latency gauges)
- [x] AdaptiveLearningRunner: implicit feedback ingestion from `mycelium_metrics`
- [x] AdaptiveLearningRunner: agent reliability trust loop + query-gap detection
- [x] AdaptiveLearningRunner: feedback-based ranking auto-tuning (`agent.metadata.ranking_adjustment`)
- [x] AdaptiveLearningRunner: summary/trend extraction meta-facts from version churn
- [x] Embedding quality benchmark script (`benchmarks/embedding_quality.py`)
