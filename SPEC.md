# Multi-Agent Memory System — Top-Level Specification

_Version: 0.3 — 2026-03-10_
_Authors: Tobias + Jasper_
_Status: Draft — reviewed, all open questions resolved_
_Project name: **Mycelium**_

---

## 1. What This Is

A standalone, open-sourceable framework that gives multi-agent systems a **shared nervous system** for memory and coordination. Not a plugin. Not a wrapper around a vector database. A new primitive for how AI agents remember, learn from each other, and stay aligned.

**One sentence**: The missing coordination layer between AI agents — making multi-agent systems that actually get smarter together, not just individually.

**Name**: **Mycelium** — the underground fungal network that propagates signals and nutrients between organisms in a forest. That's exactly what this does between agents.

---

## 2. The Problem

Every existing agent memory system (MemGPT, Zep, mem0, Cognee, etc.) treats memory as a **single-agent concern**. An agent stores things, an agent retrieves things. When you have multiple agents — which is the reality of every serious AI deployment — you get:

- **Knowledge silos**: Agent A learns something. Agent B has no idea. The human has to be the message bus.
- **Contradictory beliefs**: Agent A thinks X is true. Agent B thinks Y. Nobody detects the conflict.
- **Stale state**: Agent reports something as broken that was fixed hours ago. No verification against reality.
- **Distributed hallucination**: One agent hallucinates a fact. It gets stored. Other agents retrieve it as ground truth. The lie propagates.
- **Cold start every session**: Agents start from near-zero each time. Memory files help, but they're static, unstructured, and never complete.
- **No learning, just storage**: Agents store facts. They don't learn patterns, extract preferences from behavior, or improve their own processes.

These are not edge cases. They are the **daily reality** of running multi-agent systems. We have documented them extensively in our own setup (see FRUSTRATION_AUDIT.md, AGENT_MEMORY_RESEARCH.md).

The academic literature confirms this: as of March 2026, **no published system, framework, or paper** solves cross-agent memory propagation, distributed consistency for agent knowledge, or relevance-based selective sharing. These are fundamental gaps in the field.

---

## 3. What We're Building

A **memory and coordination system** for multi-agent AI setups with five core capabilities that no existing system provides:

### 3.1 Cross-Agent Knowledge Propagation

When Agent A learns something, relevant agents are automatically notified — not via polling, not via shared files, but via **event-driven push** based on relevance subscriptions.

- Agents subscribe to topics/domains they care about
- New facts are evaluated for relevance to each subscriber
- Propagation is selective — not everything goes everywhere
- Facts carry provenance: who learned it, when, from what source, with what confidence

### 3.2 Shared Knowledge Graph with Temporal Awareness

A central knowledge representation where facts have:

- **Temporal validity**: when did this become true? When did it stop being true?
- **Provenance chains**: who discovered it → who refined it → who uses it
- **Confidence scores**: how certain are we, based on source reliability and corroboration
- **Causal links**: this fact was derived from these other facts

Not a flat vector store. Not an unstructured document dump. A **structured, temporal, provenance-tracked knowledge graph** that agents can reason over.

### 3.3 Conflict Detection and Resolution

When agents hold contradictory beliefs about the same entity or fact:

1. **Detection**: The system identifies the contradiction automatically
2. **Temporal analysis**: Which fact is newer? What was the state at each point in time?
3. **Provenance analysis**: Which fact has stronger sourcing? Human-corrected vs. agent-inferred?
4. **Resolution**: Deterministic resolution for simple cases (CRDT-style). LLM-assisted debate for complex cases. Human escalation when confidence is low.
5. **Propagation**: The resolved fact replaces the contradictory ones across all agents

### 3.4 Trust and Quality Model

Every fact in the system has a quality envelope:

- **Trust score**: Based on source reliability, corroboration count, historical accuracy of the contributing agent
- **Confidence score**: How certain is this fact? Verified against system state vs. inferred vs. hallucinated?
- **Persistence score**: How long should this fact survive? Based on access frequency, recency, and relevance
- **Decay model**: Facts that are never accessed, never corroborated, or contradicted by newer evidence decay and eventually expire

Trust is **not flat**. Human corrections > verified system state > agent-extracted facts > agent-inferred patterns. This hierarchy is enforced, not suggested.

**Meta-insights feed trust scoring.** Operational data analyzed externally can be ingested as facts about agents themselves. Example: "jasper-trader has a 15% contradiction rate — 3x higher than average" is a fact with `subject: "jasper-trader", predicate: "has_contradiction_rate", object: "0.15"`. This fact directly influences jasper-trader's `trust_history`, lowering the trust score of future facts from that agent. This creates a feedback loop: agents that produce unreliable facts gradually become less trusted, and their facts require more corroboration before propagation.

### 3.5 Verification Layer

Before a fact is admitted to shared memory or propagated to other agents, it passes through verification gates:

- **Ground-truth check**: Can this fact be verified against actual system state? (e.g., "service X is down" — is it actually down?)
- **Contradiction check**: Does this fact contradict existing high-confidence facts?
- **Hallucination check**: Is this fact supported by the source material the agent claims to have used?
- **Staleness check**: Is this fact about something that changes frequently? If so, when was it last verified?

Facts that fail verification are flagged, quarantined, or rejected — not silently admitted.

---

## 4. What We're NOT Building

- **Not an agent framework**: We don't run agents, route tasks, or manage agent lifecycles. We provide memory and coordination to whatever framework you use.
- **Not a RAG pipeline**: We don't chunk documents and retrieve them. We manage structured, evolving knowledge.
- **Not a chat memory**: We don't just remember what was said in conversations. We maintain a living knowledge graph that agents actively use and contribute to.
- **Not a database**: We're a semantic layer on top of storage, not the storage itself. We're agnostic to whether the underlying store is Postgres, Neo4j, Supabase, or something else.

---

## 5. Architecture Principles

### 5.1 Hybrid LLM Dependency

- **Core operations are deterministic**: Store, retrieve, propagate, expire, subscribe — these do not require LLM calls. They are fast, predictable, and auditable.
- **Enrichment operations use LLMs**: Fact extraction from conversations, complex conflict resolution, relevance scoring for propagation, knowledge consolidation. These are asynchronous and optional.
- **The system works without LLMs at reduced capability**: You can store and retrieve facts, propagate changes, and run basic conflict resolution without any LLM. LLMs make it smarter, not functional.

### 5.2 Library-First, Server When Needed

- **Primary interface**: Import as a library in your agent's language (Python first, TypeScript second)
- **Server mode**: For distributed setups (agents on different machines, high-throughput setups), the same core runs as a standalone service with an API
- **Same core**: The library and the server run identical logic. The server is the library with a network layer.

### 5.3 Start Specific, Abstract Later

- **First integration**: Our own OpenClaw/Jasper multi-agent setup
- **Abstraction point**: Once we've validated the core on real workloads, we extract the agent-agnostic protocol
- **Goal**: Clean SDK that any agent framework (LangChain, CrewAI, AutoGen, custom) can integrate in <50 lines of code

### 5.4 Mycelium Boundary Rule

**All agent interaction with the knowledge graph goes through Mycelium's Python API. Never Supabase directly. No exceptions.**

If an agent needs something Mycelium doesn't expose, the right fix is to add it to Mycelium's API — not to bypass Mycelium and query Postgres directly. This is the single discipline that makes future abstraction (different storage backends, open-source release, server mode) possible without a rewrite.

This rule exists because it *will* be tempting to skip Mycelium for a quick Supabase query under time pressure. Every time that happens, the boundary erodes and the cost of future abstraction increases. Write it down so it can be enforced.

### 5.5 Operational Logging is Separate

Mycelium does NOT use itself for operational observability. Memory facts are agent knowledge. Operational data (ingestion rates, conflict counts, propagation latency, errors) goes to a **separate, append-only log** — structured logging to a separate Postgres schema or stdout → log aggregator.

Reason: circular dependencies are debugging hell. If Mycelium has a bug that corrupts facts, and that bug is logged as a fact in Mycelium, you've corrupted your own error log.

**Exception**: Meta-insights derived from operational data *can* be ingested as facts by an external analysis step. E.g., "jasper-trader has a 15% contradiction rate — 3x higher than average" is a fact about an agent that feeds into trust scoring. But it's generated externally, not by Mycelium itself.

### 5.6 Observability as Day-1 Architecture

Every memory operation is logged with:

- What happened (store, retrieve, propagate, conflict, expiry)
- Who initiated it (which agent)
- What was the result (success, conflict detected, verification failed)
- What was the latency
- What was the downstream impact (which agents received propagated facts)

This is not an add-on. It is how we measure whether the system works and how we improve it.

---

## 6. Core Concepts

### 6.1 Fact

The atomic unit of knowledge in the system. A fact is not a raw string — it is a structured object:

```
Fact {
  id:              uuid
  content:         structured knowledge (see 6.1.1 below)
  source_agent:    which agent produced this fact
  source_type:     human_correction | system_verification | agent_extraction | agent_inference
  confidence:      0.0 - 1.0
  trust_score:     computed from source_type + corroboration + history
  valid_from:      when this fact became true (event time)
  valid_until:     when this fact stopped being true (null = still valid)
  created_at:      when the system learned this fact (system time)
  provenance:      chain of fact IDs this was derived from
  tags:            topic/domain labels for subscription matching
  verification:    last verification result + timestamp
  corroborations:  list of agents that have independently confirmed this
  embedding:       vector representation for semantic search
  metadata:        JSONB — extensible, for anything not covered above
}
```

#### 6.1.1 Fact Content Model (Typed Core + JSONB)

Facts are NOT free-form strings. The content field is structured:

```
FactContent {
  subject:         what entity this fact is about (e.g., "API /v2/orders", "jasper-trader")
  predicate:       what the fact states (canonical enum or free-text — see below)
  object:          the value/target (e.g., "/v3/orders", "true", "conservative strategy")
  context:         optional — when/where this applies (e.g., "in production", "during market hours")
}
```

#### 6.1.1a Predicate Model: Canonical Enum + Embedding Similarity

Predicates are NOT a free-form ontology. They follow a hybrid model:

**Canonical predicates** are a fixed enum used for system operations — the operations where Mycelium needs to structurally understand the predicate. These are the only predicates Mycelium reasons over directly:

```
CanonicalPredicates {
  has_status       // "is_down", "is_healthy", "is_degraded"
  moved_to         // endpoint/resource relocation
  deprecated       // resource no longer recommended
  prefers          // agent or user preference
  has_limit        // rate limits, quotas, thresholds
  depends_on       // runtime dependency
  version_is       // version of a service/API/tool
  configured_as    // configuration value
  located_at       // file path, URL, endpoint
  owned_by         // ownership/responsibility
}
```

Each canonical predicate has **aliases** — strings that map to it. `"is_down"`, `"unavailable"`, `"offline"` all map to `has_status`. Alias mapping is deterministic (lookup table), not LLM-assisted.

**Free-text predicates** are allowed for everything else. They are stored as-is and matched via **embedding similarity**, not string equality. "service_is_down" and "unavailable" have near-identical embeddings — contradiction detection finds them via vector similarity on the full fact embedding (`subject + predicate + object`), not via predicate string matching.

This avoids two failure modes:
- **Predicate-proliferation** (the RDF problem): synonymous predicates that are structurally incomparable. Solved by embedding similarity.
- **Premature ontology** (the enterprise knowledge management problem): a comprehensive predicate taxonomy that never pays for itself. Solved by keeping the canonical set to ~10 system-critical predicates.

Relations between facts use fixed types:
- `CONTRADICTS` — this fact conflicts with another fact
- `SUPERSEDES` — this fact replaces an older fact
- `DERIVED_FROM` — this fact was inferred from other facts
- `CORROBORATES` — this fact independently confirms another fact
- `DEPENDS_ON` — this fact is only valid if another fact is valid

Everything else goes in `metadata` (JSONB). This gives queryable structure on the operations that matter without premature rigidity.

#### 6.1.2 What Constitutes a Fact — Guidance

Without clear guidance, ingestion quality will vary wildly between agents. A fact must:

1. **Be atomic**: One claim per fact. "API moved and auth changed" is two facts.
2. **Be falsifiable**: It must be possible to verify or contradict. "Things feel slow" is not a fact. "Query latency is >500ms on /api/orders" is.
3. **Have a clear subject**: Every fact is about a specific entity. No orphan claims.
4. **Be timestampable**: It must make sense to ask "when did this become true?"

Examples of **good facts**:
- `{subject: "/v2/orders", predicate: "deprecated", object: "true", context: "production"}`
- `{subject: "jasper-trader", predicate: "prefers", object: "conservative_strategy"}`
- `{subject: "Supabase", predicate: "rate_limit", object: "500 req/min"}`

Examples of **not facts** (don't ingest these):
- Opinions without evidence ("I think the API is slow")
- Transient state that changes per-second ("current CPU usage is 34%")
- Raw conversation fragments ("user said they want X" — extract the preference instead)
- Duplicates of existing facts with different wording

### 6.2 Agent Identity

Each agent registered in the system has:

```
AgentIdentity {
  id:              unique identifier
  role:            what this agent does (trader, researcher, planner, etc.)
  subscriptions:   static topic/domain subscriptions (see 6.3)
  trust_history:   track record of fact accuracy
  active_context:  dynamic — what this agent is currently working on (see 6.2.1)
}
```

#### 6.2.1 Active Context (Dynamic Relevance)

Subscriptions are static configuration — "I'm a trader, I always care about API, prices, risk." But relevance is dynamic — a trader mid-EURUSD-trade cares about EURUSD liquidity far more than an idle trader.

Active context captures this:

```
ActiveContext {
  task:            what the agent is doing right now (free-text or structured)
  entities:        specific entities the agent is working with (e.g., ["EURUSD", "Binance API"])
  urgency:         normal | elevated | critical
  updated_at:      when the agent last updated this
}
```

The propagation engine matches against both:
- **Static subscriptions**: baseline filter (does this agent care about this topic at all?)
- **Active context**: relevance boost (is this fact especially relevant to what the agent is doing *right now*?)

**Ownership**: The agent itself owns its active_context and updates it via:

```
update_context(agent_id, new_context) → void
```

If an agent never calls `update_context`, it still receives propagations based on static subscriptions. The worst case for a stale context is slightly suboptimal relevance ranking — not missed facts.

### 6.3 Subscription

How agents declare what knowledge is relevant to them:

```
Subscription {
  agent_id:        who subscribes
  topic:           topic/domain pattern (supports wildcards)
  priority:        how urgently this agent needs updates on this topic
  filter:          optional conditions (min confidence, source type, etc.)
}
```

### 6.4 Propagation Event

When a fact is created or updated, the system evaluates subscriptions and generates:

```
PropagationEvent {
  fact_id:         the fact being propagated
  target_agents:   list of agents that should receive this
  reason:          why each agent was selected (subscription match, relevance score)
  priority:        based on subscription priority + fact confidence
  delivered:       delivery status per agent
}
```

---

## 7. Key Operations

### 7.1 Ingest

An agent submits new knowledge to the system.

```
ingest(agent_id, raw_content, source_type, context) → Fact | RejectionReason
```

Pipeline:
1. **Extract** structured fact from raw content (LLM-assisted if enabled, otherwise pass-through)
2. **Verify** against existing knowledge (contradiction check, ground-truth check if available)
3. **Score** initial confidence and trust based on source type and agent history
4. **Store** in knowledge graph with full provenance
5. **Propagate** to subscribed agents based on relevance matching
6. Return created Fact or rejection reason

### 7.2 Query

An agent asks the system for relevant knowledge.

```
query(agent_id, question, filters?) → [Fact] ranked by relevance
```

Pipeline:
1. **Parse** query intent (semantic + structural)
2. **Retrieve** candidate facts via hybrid search (semantic similarity + graph traversal + keyword)
3. **Filter** by temporal validity (no expired facts), confidence threshold, access permissions
4. **Rank** by relevance to agent's current context + query + fact quality
5. Return ranked facts with provenance metadata

### 7.3 Correct

A human or authoritative source overrides an existing fact.

```
correct(authority_source, fact_id, new_content, reason) → UpdatedFact
```

Pipeline:
1. **Invalidate** old fact (set valid_until, mark as superseded)
2. **Create** new fact with source_type = human_correction, max trust
3. **Propagate** correction to ALL agents that received the original fact
4. **Log** correction event for learning (pattern: what was wrong, why)

### 7.4 Verify

Proactive check of existing facts against ground truth.

```
verify(fact_id, verification_method) → VerificationResult
```

Methods:
- **System probe**: Actually check if the claimed state is true (API call, file check, etc.)
- **Cross-agent corroboration**: Ask other agents if they can confirm
- **Temporal check**: Is this fact about something that should have changed by now?
- **Source re-check**: Is the original source still saying the same thing?

### 7.5 Decay

Periodic process that evaluates fact health.

```
decay_cycle() → [ExpiredFacts, DegradedFacts]
```

Rules:
- Facts not accessed in N days: confidence degrades
- Facts not corroborated by any agent: trust degrades
- Facts about volatile topics with no recent verification: flagged for re-verification
- Facts below minimum threshold: expired and removed from active graph
- Facts with human corrections never decay (pinned)

### 7.6 Agent Restart / Replay Contract

When an agent restarts (crash, redeployment, new session), it must catch up on everything it missed. The system guarantees this via a persistent event log:

```
replay(agent_id, since: timestamp) → [PropagationEvent] ordered by time
```

Contract:
1. Every propagation event is persisted in Postgres before being pushed via Supabase Realtime
2. On startup, an agent calls `replay(agent_id, last_seen_timestamp)` to get all events since its last active moment
3. The system tracks `last_ack_timestamp` per agent — the last event the agent confirmed receiving
4. If an agent has been down for >N hours, it gets a **consolidated replay** (deduplicated, superseded facts removed) instead of raw event replay — to avoid flooding with stale intermediate states
5. Agents that have never connected get a **full graph snapshot** filtered by their subscriptions

This is what makes Postgres event log + Supabase Realtime the right transport choice: real-time push when online, guaranteed replay when offline. No lost events.

### 7.7 Concurrent Write Contention

Two agents ingesting contradictory facts simultaneously is a real scenario — not an edge case. The system handles this:

```
Example:
  t=0.000: Agent A ingests "service X is down"
  t=0.050: Agent B ingests "service X is healthy"
  Both are in-flight simultaneously.
```

Resolution strategy:

1. **Optimistic concurrent ingest**: Both facts are admitted to the store. Neither blocks the other. Ingestion is not serialized.
2. **Contradiction detection runs post-commit**: After both facts are stored, the async contradiction detector identifies the conflict within the same cycle.
3. **Conflict record created**: A `Conflict` object links the two facts with their respective provenance, timestamps, and confidence scores.
4. **Deterministic first-pass resolution**: If one fact has strictly higher authority (e.g., `human_correction` vs. `agent_inference`), resolve immediately. If temporal ordering is clear and both have equal authority, the newer fact wins by default.
5. **Escalation for ambiguous cases**: If both facts have equal authority and overlapping timestamps, the conflict is flagged for LLM-assisted resolution (Phase 4) or human review.
6. **No silent overwrites**: At no point does one agent's fact silently replace another's. Every resolution is logged, traceable, and reversible.

This is fundamentally different from "last write wins" (data loss) or "first write wins" (starvation). Both perspectives are captured, and resolution is explicit.

### 7.8 Legacy Migration

One-time import of existing knowledge into Mycelium. This is a Phase 1 deliverable — without it, we can't run integration tests against our own setup.

```
migrate(source, mapping_config) → [Fact] with migration metadata
```

Sources and strategy:

1. **LanceDB semantic memories** → Batch-ingest as facts with `source_type: agent_extraction`, `confidence: 0.7`. Embeddings are recomputed (we're changing embedding model, old vectors are useless).
2. **Supabase `shared_learnings`** → Map to facts with provenance from the originating agent. Relatively clean mapping since it's already structured.
3. **Memory files (MEMORY.md etc.)** → LLM-assisted extraction to structured facts. The only migration step that requires LLM. These files are messy and need parsing.

Rules:
- All migrated facts start at **reduced confidence** (0.7 by default). They haven't been through Mycelium's verification pipeline.
- Migrated facts **can decay naturally** if they're stale — this is a feature, not a bug.
- Migration is a **cutover, not a gradual sync**. When Mycelium is live, old sources are frozen. No dual-write.
- Each migrated fact carries `metadata: {migrated_from: "lancedb"|"supabase"|"memory_file", migration_date: ...}` for traceability.

---

## 8. What Success Looks Like

### 8.1 Measurable Outcomes

| Metric | Current State | Target |
|---|---|---|
| Cross-agent knowledge latency | Minutes to never (polling/manual) | Seconds (event-driven) |
| Stale state incidents | Regular (no detection) | Near-zero (verification layer) |
| Hallucinated facts in shared memory | Undetected | Detected and quarantined |
| Cold start knowledge coverage | ~30-50% (static memory files) | >90% (pre-session injection from live graph) |
| Contradiction detection rate | 0% (no mechanism) | >95% at ingest time |
| Fact provenance traceability | None | 100% (every fact has full chain) |

### 8.2 Qualitative Outcomes

- A human corrects Agent A → Agent B, C, D know within seconds, without the human repeating themselves
- Agent A discovers that API endpoint X changed → all agents using that endpoint are notified
- An agent hallucinates a fact → the system catches it before it propagates
- A new agent joins the system → it has immediate access to the collective knowledge graph, with the right filters for its role
- After 30 days of operation, the system has measurably fewer stale-state incidents, faster coordination, and less human repetition than day 1

---

## 9. Scope and Phases

### Phase 1: Core Memory Layer

- Fact model with temporal validity and provenance
- Ingest → store → retrieve pipeline
- Basic confidence/trust scoring
- Contradiction detection at ingest time — detected conflicts create a `Conflict` record and flag both facts with `conflict_status: unresolved`. Both facts remain queryable but are returned with conflict metadata. No automatic resolution in this phase.
- Single-process library mode
- **Legacy migration**: One-time importer for existing knowledge (LanceDB, Supabase shared_learnings, memory files). Migrated facts start at `confidence: 0.7` and can decay naturally. See 7.8.
- Integration with our own agent setup as validation

### Phase 2: Propagation, Subscriptions, and Simple Resolution

- Agent identity and subscription model (with `active_context` — see 6.2)
- Event-driven propagation engine (Supabase Realtime + Postgres event log)
- Relevance-based selective sharing (static subscriptions + dynamic context matching)
- Correction propagation (human overrides)
- **Deterministic conflict resolution for simple cases**: source-type hierarchy (`human_correction > system_verification > agent_extraction > agent_inference`), temporal ordering as tiebreaker. Resolves ~60-70% of conflicts without LLM.
- **Agent restart / replay contract** (see 7.6)
- **Concurrent write contention handling** (see 7.7)
- Observability dashboard (what propagated where, why)

### Phase 3: Verification and Trust

- Ground-truth verification hooks (pluggable verification methods)
- Cross-agent corroboration
- Trust history per agent (including meta-insights — see 3.4)
- Decay and garbage collection
- Hallucination detection at ingest

### Phase 4: Advanced Conflict Resolution and Consistency

- LLM-assisted resolution for complex conflicts (ambiguous cases from Phase 2)
- Causal provenance chains
- Distributed consistency model for multi-process/multi-machine setups

### Phase 5: Open Source and Generalization

- Agent-framework agnostic SDK (Python + TypeScript)
- Server mode with API
- Documentation and examples
- Benchmark suite for multi-agent memory evaluation
- Community release

---

## 10. Technical Stack (Decided)

- **Language**: Python (primary), TypeScript (secondary SDK)
- **Storage**: PostgreSQL + pgvector via Supabase. Bi-temporal fact model in relational tables, embeddings in pgvector. No Neo4j until Phase 4 proves it necessary.
- **Embeddings**: Pluggable `embed(text) → vector` interface. Default: `text-embedding-3-small` via API. Swap to local sentence-transformers if cost demands it.
- **Transport**: Supabase Realtime for push notifications + Postgres event log for persistence/replay. In-process events for library mode.
- **LLM**: Pluggable via standard interface. Default: Claude API. Optional for all core operations (ingest, query, propagate, decay). Required for enrichment (extraction from conversations, complex conflict resolution).
- **Deployment**: Library (single-process, primary) and server (multi-process/multi-machine, Phase 5).
- **Performance targets**: Ingest <100ms (without LLM enrichment), query <50ms, propagation <1s end-to-end.
- **No vendor lock-in**: Every component is swappable. Storage, LLM, embedding model, transport layer. But we start opinionated (Supabase/Postgres) and abstract later.

---

## 11. Decisions (Resolved)

_Resolved 2026-03-10 based on team review._

### D1 — Project Name: Mycelium
The underground fungal network that propagates signals and nutrients between organisms in a forest. Avoids overused names (Nexus, Core, Hub).

### D2 — Graph Model: Postgres + pgvector
Start with Postgres + pgvector. Not Neo4j. We already use Supabase. Bi-temporal queries (Graphiti-style) are standard SQL. Full graph traversal is Phase 4 functionality — migrate there when we actually hit the limit, not before.

### D3 — Embedding Model: Pluggable, start with text-embedding-3-small
Design the embedding interface as a pluggable function from day 1: `embed(text) → vector`. Start with `text-embedding-3-small` via API — cheap, fast, good enough. Local sentence-transformers is a swap you make if cost becomes a problem. The abstraction costs nothing.

### D4 — Transport: Supabase Realtime + Postgres Event Log
Not WebSockets or SSE directly. Supabase Realtime as push notification (already in our stack) + Postgres as persistent event log that agents can replay from on restart. WebSockets have no persistence guarantee — if an agent is down when a fact propagates, it loses it. This combination solves both real-time push and crash recovery. No new infrastructure.

### D5 — Fact Schema: Typed Core + JSONB Metadata
Not free-form (too noisy), not rigid schema (too much friction in Phase 1). Concrete: fixed relation types (`CONTRADICTS`, `SUPERSEDES`, `DERIVED_FROM`, `CORROBORATES`, `DEPENDS_ON`) + JSONB metadata for what we haven't anticipated. Tags as in the spec. This gives queryable structure on the operations that matter.

### D6 — Test Harness: Declarative Scenario Runner
Not a generic agent-swarm simulator. A scenario runner with declarative YAML test cases:

```yaml
scenario: "cross-agent propagation"
steps:
  - at: t=0
    agent: jasper-code
    action: ingest
    fact: "API endpoint /v2/orders moved to /v3/orders"
    tags: [api, infrastructure]
  - at: t=1s
    assert:
      agent: jasper-trader
      knows: "API endpoint /v2/orders moved to /v3/orders"
  - at: t=2s
    agent: jasper-research
    action: ingest
    fact: "API endpoint /v2/orders is still active"
    tags: [api, infrastructure]
  - at: t=3s
    assert:
      conflict_detected: true
      between: [jasper-code, jasper-research]
```

Runs against the actual memory system. No LLMs in the test loop. Deterministic, fast, CI-friendly. Real integration testing happens against our own Jasper setup — but that's too slow for feedback loops.

---

## 12. References

Built on research from AGENT_MEMORY_RESEARCH.md. Key influences:

- **Graphiti/Zep**: Temporal knowledge graph model, bi-temporal data
- **Collaborative Memory (ICML 2025)**: Access control patterns, provenance model
- **CodeCRDT**: CRDT-based consistency for multi-agent coordination
- **KARMA (NeurIPS 2025)**: LLM-based debate for conflict resolution
- **MARK**: Trust and persistence scoring
- **A-MEM (NeurIPS 2025)**: Retroactive memory self-refinement
- **Nemori**: Predict-calibrate driven learning
- **SAGE**: Ebbinghaus forgetting curve for memory decay

---

_This spec defines WHAT we're building and WHY. Architecture design (HOW) is the next document._
