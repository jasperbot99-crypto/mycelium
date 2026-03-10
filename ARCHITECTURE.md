# Mycelium — Architecture Design

_Version: 0.1 — 2026-03-10_
_Authors: Tobias + Jasper_
_Status: Draft_
_Companion to: [SPEC.md](./SPEC.md) (what/why) — this document covers the HOW._

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Agent Processes                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │ Agent A   │  │ Agent B   │  │ Agent C   │  │ Agent N   │           │
│  │ (trader)  │  │ (code)    │  │ (research)│  │ (...)     │           │
│  └────┬──────┘  └────┬──────┘  └────┬──────┘  └────┬──────┘           │
│       │              │              │              │                  │
│  ┌────▼──────┐  ┌────▼──────┐  ┌────▼──────┐  ┌────▼──────┐           │
│  │ Mycelium  │  │ Mycelium  │  │ Mycelium  │  │ Mycelium  │           │
│  │ Client    │  │ Client    │  │ Client    │  │ Client    │           │
│  └────┬──────┘  └────┬──────┘  └────┬──────┘  └────┬──────┘           │
│       │              │              │              │                  │
└───────┼──────────────┼──────────────┼──────────────┼──────────────────┘
        │              │              │              │
        ▼              ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Mycelium Core                                 │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │  Ingest      │  │  Query       │  │  Propagation │               │
│  │  Pipeline    │  │  Engine      │  │  Engine      │               │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘               │
│         │                 │                 │                        │
│  ┌──────▼─────────────────▼─────────────────▼───────┐               │
│  │              Fact Store (Domain Layer)            │               │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐    │               │
│  │  │ Conflict   │ │ Trust &    │ │ Decay      │    │               │
│  │  │ Detector   │ │ Scoring    │ │ Manager    │    │               │
│  │  └────────────┘ └────────────┘ └────────────┘    │               │
│  └──────────────────────┬───────────────────────────┘               │
│                         │                                           │
│  ┌──────────────────────▼───────────────────────────┐               │
│  │           Storage Abstraction Layer               │               │
│  │  ┌─────────┐  ┌─────────┐  ┌──────────────┐     │               │
│  │  │ Fact    │  │ Event   │  │ Embedding    │     │               │
│  │  │ Repo    │  │ Log     │  │ Store        │     │               │
│  │  └─────────┘  └─────────┘  └──────────────┘     │               │
│  └──────────────────────┬───────────────────────────┘               │
│                         │                                           │
│  ┌──────────────────────▼───────────────────────────┐               │
│  │           Transport Layer                         │               │
│  │  ┌──────────────┐  ┌──────────────┐              │               │
│  │  │ In-Process   │  │ Supabase     │              │               │
│  │  │ Events       │  │ Realtime     │              │               │
│  │  └──────────────┘  └──────────────┘              │               │
│  └──────────────────────────────────────────────────┘               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    PostgreSQL (Supabase)                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │ facts    │  │ agents   │  │ events   │  │ conflicts│           │
│  │ (+ pgvec)│  │          │  │          │  │          │           │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘           │
│  ┌──────────┐  ┌──────────┐                                        │
│  │ subs     │  │ ops_log  │                                        │
│  │          │  │ (separate)│                                        │
│  └──────────┘  └──────────┘                                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Layer Architecture

Mycelium is organized in four distinct layers. Dependencies flow downward only — upper layers depend on lower layers, never the reverse.

### Layer 1: Storage Abstraction

The bottom layer. Owns all database interaction. No SQL leaks above this boundary.

**Components:**
- **FactRepository** — CRUD for facts, temporal queries, semantic search via pgvector
- **EventLog** — Append-only propagation event log (write, read-since)
- **AgentRepository** — Agent registration, subscription management, active context
- **ConflictRepository** — Conflict records, resolution status tracking
- **EmbeddingStore** — Vector storage and similarity search (delegates to pgvector)

**Interface principle:** Repository methods return domain objects (Fact, Agent, Conflict), not database rows. All SQL is contained here.

**Pluggability:** The storage layer is defined by abstract interfaces (Python `Protocol` classes). The default implementation targets Supabase/Postgres. Swapping to a different backend means implementing the same protocols — no changes above.

### Layer 2: Domain Logic (Fact Store)

The core intelligence layer. All business rules live here. Stateless — receives dependencies via constructor injection.

**Components:**
- **ConflictDetector** — Identifies contradictions between facts using embedding similarity + canonical predicate matching. Two facts conflict when they share a subject, have semantically similar predicates, but contradictory objects. Outputs `Conflict` records.
- **TrustScorer** — Computes trust and confidence scores based on source type hierarchy, corroboration count, and agent trust history. Pure function: `(fact, agent_history, corroborations) → scores`.
- **DecayManager** — Evaluates fact health. Applies Ebbinghaus-inspired decay curves based on access frequency, recency, corroboration status, and topic volatility. Pinned facts (human corrections) are exempt.
- **PredicateResolver** — Maps free-text predicates to canonical predicates via alias lookup table. Falls back to embedding similarity for unknown predicates.

### Layer 3: Pipelines (Ingest, Query, Propagation)

Orchestration layer. Composes domain logic components into end-to-end operations. This is where the spec's "Key Operations" (Section 7) become concrete code paths.

**Components:**
- **IngestPipeline** — The full ingest flow: extract → verify → score → store → propagate
- **QueryEngine** — Parse → retrieve → filter → rank → return
- **PropagationEngine** — Subscription matching + active context relevance → event creation → delivery
- **CorrectionPipeline** — Invalidate → create superseding fact → force-propagate to all prior recipients
- **ContradictionSweeper** — Post-commit background sweep for contradictions missed by pre-commit check (see D-ARCH-3). Runs on configurable interval.
- **VerificationRunner** — Pluggable verification methods, scheduled or on-demand (Phase 3)
- **DecayCycleRunner** — Periodic sweep that evaluates all active facts via DecayManager (Phase 3)

### Layer 4: Client SDK (Public API)

The surface agents interact with. Thin — validates input, delegates to pipelines, returns results.

```python
class MyceliumClient:
    """The single entry point for agents."""

    def __init__(self, agent_id: str, config: MyceliumConfig): ...

    # Core operations
    async def ingest(self, content: FactContent, source_type: SourceType,
                     tags: list[str], context: str | None = None) -> Fact | RejectionReason: ...
    async def query(self, question: str, filters: QueryFilters | None = None) -> list[Fact]: ...
    async def correct(self, fact_id: UUID, new_content: FactContent, reason: str) -> Fact: ...

    # Context management
    async def update_context(self, context: ActiveContext) -> None: ...

    # Subscriptions (typically set at init, can be updated)
    async def subscribe(self, topic: str, priority: Priority,
                        filter: SubscriptionFilter | None = None) -> Subscription: ...
    async def unsubscribe(self, subscription_id: UUID) -> None: ...

    # Replay (called on agent startup)
    async def replay(self, since: datetime | None = None) -> list[PropagationEvent]: ...

    # Event listener (for real-time propagation)
    def on_fact(self, callback: Callable[[PropagationEvent], Awaitable[None]]) -> None: ...

    # Lifecycle
    async def connect(self) -> None:
        """Upserts agent record, sets last_seen_at, replays missed events."""
        ...
    async def disconnect(self) -> None: ...
```

### 4.5 Agent Registration Lifecycle

Agent registration is implicit — handled by `connect()`, not a separate registration step.

```
MyceliumClient(agent_id="jasper-trader", config=config)
    │
    ▼
client.connect()
    │
    ▼
┌────────────────┐
│ Upsert Agent   │ INSERT INTO mycelium.agents (id, role, ...)
│                │ ON CONFLICT (id) DO UPDATE SET last_seen_at = now()
│                │
│                │ If new agent: created with defaults (trust_score=0.5, etc.)
│                │ If existing: last_seen_at updated, active context preserved
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Apply Config   │ Sync subscriptions from config to mycelium.subscriptions
│ Subscriptions  │ (add new, remove stale — source of truth is config)
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Replay         │ Fetch missed PropagationEvents since last_ack_event_id
│ Missed Events  │ Deliver via on_fact callback (if registered)
└────────────────┘
```

**Constructor requires:**
- `agent_id: str` — unique identifier (e.g., `"jasper-trader"`)
- `config: MyceliumConfig` — includes database URL, embedding provider, etc.

**Constructor optionally accepts:**
- `role: str` — what this agent does (default: `"generic"`)
- `subscriptions: list[SubscriptionConfig]` — topics to subscribe to at connect time

**Key decisions:**
- **Upsert, not fail-if-missing.** First `connect()` creates the agent. Subsequent calls update `last_seen_at`. No separate registration step required.
- **Config is the source of truth for subscriptions.** On each `connect()`, subscriptions in config are synced to the database. This means agent code declares its subscriptions, not a separate admin process.
- **Role is set once, updated on re-connect if different.** If agent code changes its role string, the next `connect()` updates it.

---

## 3. Data Model (PostgreSQL Schema)

### 3.1 `mycelium.facts` — The Core Table

```sql
CREATE TABLE mycelium.facts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Structured content (6.1.1)
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    predicate_canonical TEXT,          -- NULL if no canonical match
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
    valid_from      TIMESTAMPTZ NOT NULL DEFAULT now(),  -- event time: when this became true
    valid_until     TIMESTAMPTZ,                         -- event time: when this stopped being true
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),  -- system time: when we learned this
    expired_at      TIMESTAMPTZ,                         -- system time: when we invalidated this

    -- Provenance chain
    derived_from    UUID[],            -- fact IDs this was derived from
    supersedes      UUID,              -- fact ID this replaces (for corrections)

    -- Quality signals
    corroboration_count INT NOT NULL DEFAULT 0,
    last_accessed_at    TIMESTAMPTZ,
    access_count        INT NOT NULL DEFAULT 0,
    last_verified_at    TIMESTAMPTZ,
    verification_status TEXT CHECK (verification_status IN (
                            'unverified', 'verified', 'failed', 'stale')),

    -- Classification
    tags            TEXT[] NOT NULL DEFAULT '{}',

    -- Conflict status
    conflict_status TEXT CHECK (conflict_status IN (
                        'none', 'unresolved', 'resolved')) DEFAULT 'none',

    -- Embedding (pgvector)
    embedding       vector(1536),      -- text-embedding-3-small dimension

    -- Extensible
    metadata        JSONB DEFAULT '{}',

    -- Migration tracking
    migrated_from   TEXT,              -- 'lancedb' | 'supabase' | 'memory_file' | NULL
    migration_date  TIMESTAMPTZ
);

-- Indexes
CREATE INDEX idx_facts_subject ON mycelium.facts(subject);
CREATE INDEX idx_facts_tags ON mycelium.facts USING GIN(tags);
CREATE INDEX idx_facts_embedding ON mycelium.facts
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_facts_valid ON mycelium.facts(valid_from, valid_until)
    WHERE expired_at IS NULL;
CREATE INDEX idx_facts_source_agent ON mycelium.facts(source_agent_id);
CREATE INDEX idx_facts_conflict ON mycelium.facts(conflict_status)
    WHERE conflict_status = 'unresolved';
CREATE INDEX idx_facts_predicate_canonical ON mycelium.facts(predicate_canonical)
    WHERE predicate_canonical IS NOT NULL;
```

### 3.2 `mycelium.agents`

```sql
CREATE TABLE mycelium.agents (
    id              TEXT PRIMARY KEY,   -- e.g. 'jasper-trader', 'jasper-code'
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
    last_ack_event_id UUID,            -- last propagation event this agent confirmed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    metadata        JSONB DEFAULT '{}'
);
```

### 3.3 `mycelium.subscriptions`

```sql
CREATE TABLE mycelium.subscriptions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT NOT NULL REFERENCES mycelium.agents(id),
    topic           TEXT NOT NULL,      -- supports wildcards: 'api.*', 'infrastructure'
    priority        TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'critical')),
    min_confidence  FLOAT DEFAULT 0.0,
    source_types    TEXT[],             -- filter: only these source types, NULL = all
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(agent_id, topic)
);
```

### 3.4 `mycelium.fact_relations`

```sql
CREATE TABLE mycelium.fact_relations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_fact_id  UUID NOT NULL REFERENCES mycelium.facts(id),
    target_fact_id  UUID NOT NULL REFERENCES mycelium.facts(id),
    relation_type   TEXT NOT NULL CHECK (relation_type IN (
                        'contradicts', 'supersedes', 'derived_from',
                        'corroborates', 'depends_on')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT,               -- agent or system that established this relation
    metadata        JSONB DEFAULT '{}',

    UNIQUE(source_fact_id, target_fact_id, relation_type)
);

CREATE INDEX idx_relations_source ON mycelium.fact_relations(source_fact_id);
CREATE INDEX idx_relations_target ON mycelium.fact_relations(target_fact_id);
CREATE INDEX idx_relations_type ON mycelium.fact_relations(relation_type);
```

### 3.5 `mycelium.propagation_events`

```sql
CREATE TABLE mycelium.propagation_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_id         UUID NOT NULL REFERENCES mycelium.facts(id),
    target_agent_id TEXT NOT NULL REFERENCES mycelium.agents(id),
    reason          TEXT NOT NULL,       -- why this agent was selected
    priority        TEXT NOT NULL,
    delivered       BOOLEAN NOT NULL DEFAULT FALSE,
    delivered_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- For replay ordering
    sequence_num    BIGSERIAL
);

CREATE INDEX idx_events_agent_seq ON mycelium.propagation_events(target_agent_id, sequence_num);
CREATE INDEX idx_events_undelivered ON mycelium.propagation_events(target_agent_id)
    WHERE delivered = FALSE;
```

### 3.6 `mycelium.conflicts`

```sql
CREATE TABLE mycelium.conflicts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_a_id       UUID NOT NULL REFERENCES mycelium.facts(id),
    fact_b_id       UUID NOT NULL REFERENCES mycelium.facts(id),
    status          TEXT NOT NULL CHECK (status IN (
                        'detected', 'auto_resolved', 'llm_resolved',
                        'human_resolved', 'escalated')),
    resolution      JSONB,              -- which fact won, why, what action was taken
    winning_fact_id UUID REFERENCES mycelium.facts(id),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT,               -- 'system', 'llm', agent_id, or human identifier

    metadata        JSONB DEFAULT '{}'
);
```

### 3.7 `ops.operation_log` (Separate Schema — Section 5.5)

```sql
CREATE TABLE ops.operation_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation       TEXT NOT NULL,       -- 'ingest', 'query', 'propagate', 'conflict', 'decay', 'verify'
    agent_id        TEXT,
    fact_id         UUID,
    status          TEXT NOT NULL,       -- 'success', 'failure', 'rejected', 'conflict_detected'
    latency_ms      INT,
    detail          JSONB,              -- operation-specific detail
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ops_operation ON ops.operation_log(operation, created_at);
CREATE INDEX idx_ops_agent ON ops.operation_log(agent_id, created_at);
```

---

## 4. Component Design

### 4.1 Ingest Pipeline

The most critical path. Every fact enters the system through this pipeline.

```
Agent calls ingest()
    │
    ▼
┌────────────────┐
│ Input          │ Validate FactContent fields (subject, predicate, object)
│ Validation     │ Reject malformed input immediately
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Predicate      │ Try canonical alias lookup → set predicate_canonical
│ Resolution     │ If no alias match → leave predicate_canonical NULL
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Embedding      │ embed(subject + predicate + object + context) → vector
│ Generation     │ Async, but must complete before contradiction check
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Contradiction  │ PRE-COMMIT CHECK (see D-ARCH-3 Phase A):
│ Check          │ Search existing *committed* facts with:
│ (pre-commit)   │   - Same subject (exact match)
│                │   - Similar embedding (cosine > threshold)
│                │   - Same canonical predicate (if both have one)
│                │ If conflict found → create Conflict record, flag both facts
│                │ NOTE: does NOT catch concurrent in-flight writes —
│                │       post-commit sweep (ContradictionSweeper) handles those
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Trust &        │ Score based on:
│ Confidence     │   - source_type hierarchy weight
│ Scoring        │   - agent trust_history
│                │   - corroboration (0 at ingest — first occurrence)
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Store          │ INSERT fact into mycelium.facts
│                │ INSERT relations (if derived_from provided)
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Propagate      │ Evaluate subscriptions → create PropagationEvents
│ (async)        │ Push via transport layer (in-process or Supabase Realtime)
└────────────────┘
```

**Latency target:** <100ms for the synchronous path (validation through store). Propagation is async.

**Phase 1 scope:** No LLM in the ingest path. Fact extraction from conversations is the caller's responsibility — the agent passes structured FactContent. LLM-assisted extraction is Phase 3+.

### 4.2 Query Engine

```
Agent calls query()
    │
    ▼
┌────────────────┐
│ Query          │ embed(question) → vector
│ Embedding      │
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Candidate      │ Hybrid retrieval:
│ Retrieval      │   1. Semantic: pgvector cosine similarity (top-K)
│                │   2. Structural: exact subject/predicate match
│                │   3. Tag match: if query contains known tags
│                │ Union + dedup candidates
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Filter         │ Remove:
│                │   - Expired facts (valid_until < now OR expired_at IS NOT NULL)
│                │   - Below confidence threshold (from QueryFilters)
│                │   - Non-matching source_type (if filtered)
│                │ Include conflict metadata on conflicted facts
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Rank           │ Score = weighted combination of:
│                │   - Semantic similarity to query
│                │   - Trust score
│                │   - Recency (valid_from)
│                │   - Agent context relevance (if agent has active_context)
│                │ Return top-N ranked facts with provenance
└────────────────┘
```

**Latency target:** <50ms.

### 4.3 Propagation Engine (Phase 2)

```
New fact stored
    │
    ▼
┌────────────────┐
│ Subscription   │ For each registered agent (excluding source agent):
│ Matching       │   1. Check static subscriptions: does any topic match fact tags?
│                │   2. Check active context: is fact subject in agent's active_entities?
│                │   3. Compute relevance score: subscription priority × context boost
│                │ Filter: agents with score > threshold
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Event          │ For each matched agent:
│ Creation       │   CREATE PropagationEvent (fact_id, agent_id, reason, priority)
│                │   INSERT into propagation_events table
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Delivery       │ Transport-dependent:
│                │   - In-process: invoke agent's on_fact callback directly
│                │   - Supabase Realtime: publish to agent's channel
│                │ Mark delivered on ack
└────────────────┘
```

**Topic matching:** Wildcard support via simple prefix matching. `api.*` matches `api.orders`, `api.auth`. Exact match: `infrastructure` matches only `infrastructure`. Wildcard `*` matches everything.

### 4.4 Conflict Detector

The contradiction check in the ingest pipeline deserves detailed design because it's the most algorithmically nuanced component.

**Detection strategy (Phase 1):**

```
Input: new_fact (with embedding)

1. CANDIDATE SELECTION
   Query: SELECT * FROM facts
          WHERE subject = new_fact.subject
            AND expired_at IS NULL
            AND valid_until IS NULL
            AND id != new_fact.id

   If no candidates → no conflict, return

2. SEMANTIC SIMILARITY
   For each candidate:
     similarity = cosine(new_fact.embedding, candidate.embedding)
     If similarity < CONTRADICTION_THRESHOLD → skip (unrelated facts)
     If similarity > CORROBORATION_THRESHOLD → mark as potential corroboration

   Candidates in the "middle zone" (high similarity but not near-identical)
   are potential contradictions.

3. PREDICATE ANALYSIS
   If both facts have predicate_canonical:
     Same canonical predicate + different object → CONTRADICTION
     Same canonical predicate + same object → CORROBORATION

   If one or both lack predicate_canonical:
     Use embedding similarity of (predicate + object) substrings
     High similarity with different objects → CONTRADICTION candidate

4. OUTPUT
   For each detected contradiction:
     CREATE Conflict record (fact_a, fact_b, status='detected')
     CREATE fact_relation (contradicts) bidirectionally
     SET conflict_status = 'unresolved' on both facts
```

**Thresholds:**

Phase 1 uses a **global** threshold as a starting point:
- `CONTRADICTION_THRESHOLD`: 0.75 cosine similarity (same topic area)
- `CORROBORATION_THRESHOLD`: 0.95 cosine similarity (near-identical claim)

**Known limitation:** A global threshold is naive across domains with different semantic densities. "API is down" vs. "API is up" may score ~0.7 (sharing most words) — a false negative. "BTC price is $50k" vs. "BTC price is $60k" may score ~0.9 — but these are distinct facts, not a contradiction. A single threshold will produce false positives in some domains and false negatives in others.

**Why this is acceptable in Phase 1:** Phase 1 has no auto-resolution — conflicts are only flagged, not acted upon. False positives are annoying (a non-contradiction flagged as one) but not destructive. False negatives are worse (a real contradiction missed) but will surface through queries or human review. Crucially, we don't yet have the operational data to tune per-domain thresholds — guessing them now is equally naive, just differently.

**Phase 2 plan — tag-based thresholds:**
```python
# Phase 2: threshold varies by fact tags
THRESHOLD_OVERRIDES: dict[str, float] = {
    "price": 0.92,        # Numeric values: high similarity ≠ contradiction
    "status": 0.65,       # Status facts: even moderate similarity may conflict
    "configuration": 0.80, # Config values: moderate threshold
}
# Default used when no tag override matches
DEFAULT_CONTRADICTION_THRESHOLD = 0.75
```
Threshold overrides are populated from operational data (Phase 1 conflict logs analyzed for false positive/negative rates per tag).

**Phase 2 addition:** Deterministic resolution for simple cases (source_type hierarchy, temporal ordering).

**Phase 4 addition:** LLM-assisted resolution for ambiguous cases.

---

## 5. Domain Types (Python)

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID


class SourceType(str, Enum):
    HUMAN_CORRECTION = "human_correction"
    SYSTEM_VERIFICATION = "system_verification"
    AGENT_EXTRACTION = "agent_extraction"
    AGENT_INFERENCE = "agent_inference"

    @property
    def trust_weight(self) -> float:
        """Fixed hierarchy — spec section 3.4."""
        return {
            self.HUMAN_CORRECTION: 1.0,
            self.SYSTEM_VERIFICATION: 0.85,
            self.AGENT_EXTRACTION: 0.6,
            self.AGENT_INFERENCE: 0.4,
        }[self]


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class Urgency(str, Enum):
    NORMAL = "normal"
    ELEVATED = "elevated"
    CRITICAL = "critical"


class ConflictStatus(str, Enum):
    DETECTED = "detected"
    AUTO_RESOLVED = "auto_resolved"
    LLM_RESOLVED = "llm_resolved"
    HUMAN_RESOLVED = "human_resolved"
    ESCALATED = "escalated"


class RelationType(str, Enum):
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    DERIVED_FROM = "derived_from"
    CORROBORATES = "corroborates"
    DEPENDS_ON = "depends_on"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    STALE = "stale"


@dataclass(frozen=True)
class FactContent:
    """Structured content of a fact — spec section 6.1.1."""
    subject: str
    predicate: str
    object: str
    context: str | None = None


@dataclass
class Fact:
    """The atomic unit of knowledge — spec section 6.1."""
    id: UUID
    content: FactContent
    source_agent_id: str
    source_type: SourceType
    confidence: float
    trust_score: float
    valid_from: datetime
    valid_until: datetime | None
    created_at: datetime
    expired_at: datetime | None
    derived_from: list[UUID]
    supersedes: UUID | None
    corroboration_count: int
    last_accessed_at: datetime | None
    access_count: int
    tags: list[str]
    conflict_status: str
    verification_status: VerificationStatus
    embedding: list[float] | None
    metadata: dict


@dataclass
class ActiveContext:
    """Dynamic context for relevance matching — spec section 6.2.1."""
    task: str | None = None
    entities: list[str] = field(default_factory=list)
    urgency: Urgency = Urgency.NORMAL


@dataclass
class Subscription:
    """Agent's topic subscription — spec section 6.3."""
    id: UUID
    agent_id: str
    topic: str
    priority: Priority
    min_confidence: float = 0.0
    source_types: list[SourceType] | None = None


@dataclass
class PropagationEvent:
    """A fact delivery to a specific agent — spec section 6.4."""
    id: UUID
    fact: Fact
    target_agent_id: str
    reason: str
    priority: Priority
    delivered: bool
    delivered_at: datetime | None
    sequence_num: int


@dataclass
class Conflict:
    """A detected contradiction between two facts — spec section 7.7."""
    id: UUID
    fact_a: Fact
    fact_b: Fact
    status: ConflictStatus
    resolution: dict | None
    winning_fact_id: UUID | None
    detected_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None


@dataclass
class RejectionReason:
    """Returned when ingest rejects a fact."""
    code: str       # 'invalid_content', 'duplicate', 'verification_failed'
    message: str
    existing_fact_id: UUID | None = None  # if rejected due to duplicate
```

---

## 6. Embedding Strategy

### 6.1 Pluggable Interface

```python
from typing import Protocol


class EmbeddingProvider(Protocol):
    """Spec section D3 — pluggable embed(text) → vector."""

    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dimension(self) -> int: ...
```

### 6.2 Default Implementation

```python
class OpenAIEmbedding:
    """Default: text-embedding-3-small via API."""

    model = "text-embedding-3-small"
    dimension = 1536

    async def embed(self, text: str) -> list[float]:
        # OpenAI API call
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Batch API call (max 2048 inputs per request)
        ...
```

### 6.3 What Gets Embedded

Each fact produces one embedding from the concatenation:

```
"{subject} {predicate} {object} {context or ''}"
```

Example: `"API /v2/orders deprecated true production"` → vector(1536)

This composite embedding is used for both semantic search (query) and contradiction detection (similarity between facts).

---

## 7. Transport Layer

### 7.1 Transport Interface

```python
class Transport(Protocol):
    """Abstraction over event delivery mechanism."""

    async def publish(self, agent_id: str, event: PropagationEvent) -> None: ...
    async def subscribe(self, agent_id: str,
                        callback: Callable[[PropagationEvent], Awaitable[None]]) -> None: ...
    async def unsubscribe(self, agent_id: str) -> None: ...
```

### 7.2 In-Process Transport (Phase 1)

For single-process library mode. Simple async callback registry.

```python
class InProcessTransport:
    """Direct callback invocation within the same process."""

    def __init__(self, ops_logger: OpsLogger, callback_timeout: float = 5.0):
        self._listeners: dict[str, Callable] = {}
        self._ops = ops_logger
        self._timeout = callback_timeout

    async def publish(self, agent_id: str, event: PropagationEvent) -> None:
        if agent_id not in self._listeners:
            return
        try:
            await asyncio.wait_for(
                self._listeners[agent_id](event),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            self._ops.log("propagation_timeout", agent_id=agent_id,
                          event_id=event.id, timeout_s=self._timeout)
            # Event stays undelivered — will be retried or picked up via replay
        except Exception as e:
            self._ops.log("propagation_error", agent_id=agent_id,
                          event_id=event.id, error=str(e))
            # Continue to next agent — one failing callback must not block others

    async def subscribe(self, agent_id: str, callback) -> None:
        self._listeners[agent_id] = callback

    async def unsubscribe(self, agent_id: str) -> None:
        self._listeners.pop(agent_id, None)
```

**Error handling contract (applies to all Transport implementations):**
- A failing callback is logged to `ops.operation_log` and the event is left as `delivered = FALSE`
- A slow callback is timed out (default: 5s) — logged and treated as undelivered
- Propagation to other agents continues regardless — one agent's failure never blocks another
- Undelivered events are picked up on the agent's next `replay()` call

### 7.3 Supabase Realtime Transport (Phase 2)

Uses Supabase Realtime channels for cross-process push. Each agent gets a dedicated channel: `mycelium:agent:{agent_id}`.

Events are always persisted to the `propagation_events` table first (for replay guarantee), then published via Realtime for immediate delivery.

---

## 8. Configuration

```python
@dataclass
class MyceliumConfig:
    """Configuration for a Mycelium instance."""

    # Database
    database_url: str                          # Postgres connection string
    schema: str = "mycelium"

    # Embedding
    embedding_provider: EmbeddingProvider | None = None  # None = default OpenAI
    openai_api_key: str | None = None          # Used if embedding_provider is None

    # Transport
    transport: Transport | None = None          # None = InProcessTransport
    supabase_url: str | None = None            # Used for Supabase Realtime transport
    supabase_key: str | None = None

    # LLM (optional — for enrichment operations)
    llm_provider: str | None = None            # 'anthropic', 'openai', etc.
    llm_api_key: str | None = None

    # Tuning
    contradiction_threshold: float = 0.75      # Cosine similarity for conflict detection
    corroboration_threshold: float = 0.95      # Cosine similarity for corroboration
    contradiction_sweep_interval_s: int = 60   # Post-commit sweep interval (D-ARCH-3)
    callback_timeout_s: float = 5.0            # Transport callback timeout
    default_confidence: float = 0.5
    query_result_limit: int = 20
    decay_cycle_interval_hours: int = 24

    # Operational logging
    ops_log_enabled: bool = True
    ops_log_schema: str = "ops"
```

---

## 9. Project Structure

```
mycelium/
├── pyproject.toml
├── README.md
├── SPEC.md
├── ARCHITECTURE.md
│
├── src/
│   └── mycelium/
│       ├── __init__.py              # Public API exports
│       ├── client.py                # MyceliumClient (Layer 4)
│       ├── config.py                # MyceliumConfig
│       │
│       ├── domain/                  # Layer 2: Domain logic
│       │   ├── __init__.py
│       │   ├── types.py             # All domain types (Fact, Agent, etc.)
│       │   ├── conflict.py          # ConflictDetector
│       │   ├── trust.py             # TrustScorer
│       │   ├── decay.py             # DecayManager
│       │   └── predicates.py        # PredicateResolver + canonical alias table
│       │
│       ├── pipelines/               # Layer 3: Orchestration
│       │   ├── __init__.py
│       │   ├── ingest.py            # IngestPipeline
│       │   ├── query.py             # QueryEngine
│       │   ├── propagation.py       # PropagationEngine
│       │   └── correction.py        # CorrectionPipeline
│       │
│       ├── storage/                 # Layer 1: Storage abstraction
│       │   ├── __init__.py
│       │   ├── protocols.py         # Abstract interfaces (Protocol classes)
│       │   ├── postgres/
│       │   │   ├── __init__.py
│       │   │   ├── facts.py         # FactRepository (Postgres impl)
│       │   │   ├── agents.py        # AgentRepository
│       │   │   ├── events.py        # EventLog
│       │   │   ├── conflicts.py     # ConflictRepository
│       │   │   └── migrations/      # SQL migration files
│       │   │       ├── 001_initial.sql
│       │   │       └── ...
│       │   └── embeddings.py        # EmbeddingStore (pgvector wrapper)
│       │
│       ├── transport/               # Transport layer
│       │   ├── __init__.py
│       │   ├── protocols.py         # Transport Protocol
│       │   ├── in_process.py        # InProcessTransport
│       │   └── supabase_rt.py       # SupabaseRealtimeTransport (Phase 2)
│       │
│       ├── embeddings/              # Embedding providers
│       │   ├── __init__.py
│       │   ├── protocols.py         # EmbeddingProvider Protocol
│       │   └── openai.py            # OpenAI text-embedding-3-small
│       │
│       └── ops/                     # Operational logging
│           ├── __init__.py
│           └── logger.py            # Structured ops logger
│
├── tests/
│   ├── conftest.py                  # Shared fixtures (test DB, etc.)
│   ├── unit/
│   │   ├── test_conflict.py
│   │   ├── test_trust.py
│   │   ├── test_decay.py
│   │   ├── test_predicates.py
│   │   └── test_types.py
│   ├── integration/
│   │   ├── test_ingest.py
│   │   ├── test_query.py
│   │   ├── test_propagation.py
│   │   └── test_correction.py
│   └── scenarios/                   # Declarative YAML scenario tests (D6)
│       ├── runner.py
│       ├── cross_agent_propagation.yaml
│       ├── conflict_detection.yaml
│       └── correction_cascade.yaml
│
├── migrations/                      # Standalone SQL migrations (for server mode)
│   ├── 001_initial.sql
│   └── ...
│
└── scripts/
    ├── migrate.py                   # Run SQL migrations
    └── legacy_import.py             # Legacy migration tool (7.8)
```

---

## 10. Phase 1 Scope — What Gets Built First

Phase 1 delivers the **core memory layer** — enough to validate the architecture against real workloads.

### In scope:
1. **Domain types** — All types in section 5
2. **Storage layer** — Postgres implementation of FactRepository, AgentRepository, ConflictRepository
3. **Ingest pipeline** — Full path: validate → embed → contradiction check → score → store
4. **Query engine** — Hybrid retrieval (semantic + structural), temporal filtering, ranking
5. **Conflict detection** — Two-phase: pre-commit check in ingest path + post-commit ContradictionSweeper. Conflict record creation (no resolution)
6. **Trust scoring** — Source-type hierarchy, initial confidence assignment
7. **Predicate resolution** — Canonical alias lookup table
8. **Client SDK** — `ingest()`, `query()`, `correct()` (without propagation)
9. **Legacy migration** — Import tool for LanceDB, Supabase shared_learnings, memory files
10. **Operational logging** — Structured logging to ops.operation_log
11. **Test infrastructure** — Unit tests, integration tests against test Postgres, scenario runner skeleton

### NOT in scope (Phase 2+):
- Propagation engine and subscriptions (Phase 2)
- Active context and dynamic relevance (Phase 2)
- Replay / crash recovery (Phase 2)
- Supabase Realtime transport (Phase 2)
- Verification hooks (Phase 3)
- Decay cycle runner (Phase 3)
- LLM-assisted anything (Phase 3-4)
- Server mode (Phase 5)

---

## 11. Key Design Decisions

### D-ARCH-1: Async-First

All public API methods are `async`. Rationale: database I/O and embedding API calls are inherently async. Sync wrappers can be added for simple use cases, but the core is async.

**Library:** `asyncpg` for Postgres. `httpx` or `aiohttp` for embedding API calls.

### D-ARCH-2: No ORM

Raw SQL via `asyncpg`, not SQLAlchemy or similar. Rationale:
- The schema is stable and well-defined
- We need full control over pgvector queries and bi-temporal WHERE clauses
- ORMs add latency and obscure the actual queries
- Easier to audit and optimize

### D-ARCH-3: Two-Phase Contradiction Detection

Contradiction detection has two complementary phases. Both are necessary. Neither alone is sufficient.

**Phase A — Pre-commit check (synchronous, in the ingest path):**
Before storing a new fact, query existing *committed* facts for contradictions. If found, the conflict is flagged on the ingest response immediately. This catches the common case: a new fact contradicting established knowledge.

**Phase B — Post-commit sweep (asynchronous, periodic):**
A background sweep detects contradictions that the pre-commit check cannot catch — specifically concurrent writes where two contradictory facts are in-flight simultaneously and neither is committed when the other's pre-commit check runs. This is not a theoretical edge case; it's the scenario described in spec 7.7.

**Why both are needed:**
- Pre-commit alone gives a false guarantee. Under read-committed isolation, it only sees what's already committed. Two agents ingesting contradictory facts within the same 100ms window will both pass their pre-commit checks.
- Post-commit alone delays all conflict detection unnecessarily. The common case (new fact vs. established knowledge) should be caught immediately, not minutes later on the next sweep.

**Post-commit sweep design:**
- Runs on a configurable interval (default: 60s in Phase 1, can be tuned)
- Scans facts created since the last sweep
- Uses the same ConflictDetector logic as the pre-commit check
- Creates Conflict records for any contradictions missed by pre-commit
- Logged to ops as `contradiction_sweep` events

**Latency impact:** Pre-commit check adds ~20-40ms to ingest. Post-commit sweep is background and does not affect ingest latency.

### D-ARCH-4: Embedding Is Required

Every fact must have an embedding. It's not optional. This is a hard requirement for contradiction detection and semantic query. The ingest pipeline generates the embedding before storing the fact.

Consequence: every ingest requires an embedding API call (~50ms for OpenAI, or faster with local models). This is acceptable given the <100ms target for non-LLM ingest.

### D-ARCH-5: Schema Migrations via Raw SQL Files

No migration framework (Alembic, etc.). Numbered SQL files (`001_initial.sql`, `002_add_index.sql`) applied in order by a simple Python script. Rationale: the schema is simple, changes are infrequent, and migration frameworks add complexity we don't need in Phase 1.

### D-ARCH-6: Fact Immutability

Facts are never mutated in place. A correction creates a new fact that `supersedes` the old one. The old fact gets `expired_at` set but is never deleted. This preserves full history and audit trail.

Exception: scoring fields (`confidence`, `trust_score`, `corroboration_count`, `access_count`, `last_accessed_at`) are updated in place — these are operational metadata, not knowledge content.

---

## 12. Resolved Architecture Questions

### D-ARCH-Q1: Connection Pooling — Shared Pool via Config

Shared `asyncpg` pool passed via `MyceliumConfig`. If not provided, `connect()` creates a default pool. Multiple `MyceliumClient` instances in the same process share the pool. Standard approach, no further discussion needed.

### D-ARCH-Q2: Embedding Caching — LRU in Provider Wrapper

Size-bounded LRU cache (default: 1024 entries) wrapping the `EmbeddingProvider`. Same query string from different agents hits cache instead of API. Keyed on input text, not agent. Standard implementation.

### D-ARCH-Q3: Legacy Migration Order — Supabase First

Order is irrelevant for correctness (all migrated facts start at confidence 0.7). Start with Supabase `shared_learnings` for cleanest validation path — it's already structured. LanceDB second. Memory files last (require LLM extraction, messiest).

### D-ARCH-Q4: Test Database — Homebrew Postgres (Local), Docker Compose (CI)

Local development: Homebrew Postgres 16 + pgvector. Already installed, minimal overhead, no container runtime needed. CI: Docker Compose with the same schema — easy to add later since all DB interaction goes through an asyncpg connection string. Switching is a config change, not a code change.

---

_This document defines HOW we build Mycelium. The companion [SPEC.md](./SPEC.md) defines WHAT and WHY._
