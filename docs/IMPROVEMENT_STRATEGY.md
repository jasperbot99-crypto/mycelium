# Mycelium Improvement Strategy — From Shared Fact Store to Shared Nervous System

_Date: 2026-03-11_
_Author: Architecture Review_
_Status: Design proposal — no code changes_

---

## Executive Summary

Mycelium delivers on its Phase 1-5 promises: structured fact storage, cross-agent propagation, conflict detection, verification, decay, and a server API. But the spec's vision — "agents that actually get smarter together" — requires fundamental changes to **what data enters the system** and **how human authority flows through it**.

**Current state**: 311 active facts, 309 from a one-time MEMORY.md migration (redundant — MEMORY.md is already in agent context), 2 from live ingest. 0 verified facts. 86 daily note files across 5 agent workspaces (19,216 lines) contain the actual valuable cross-agent knowledge — and none of it reaches Mycelium. Human corrections via Telegram/main agent don't propagate to other agents.

**Two critical failures this strategy addresses first:**
1. **Wrong data source** — MEMORY.md facts are redundant. Daily notes are the real gold.
2. **No human correction flow** — When the operator corrects an agent, that correction dies in a chat log.

---

## 0. Data Source Failure — The #1 Problem

### Status Quo
309 of 311 facts come from a one-time LLM extraction of MEMORY.md. MEMORY.md is **already injected into every agent's context** by Claude Code's built-in memory system. Mycelium is re-injecting a lower-quality version of information the agent already sees. Meanwhile, 86 daily note files (19,216 lines) across 5 agent workspaces contain the actual cross-agent knowledge — trades, bug fixes, research findings, decisions — and **none of it enters Mycelium**.

The plugin only captures `memory_store` tool calls. In production: 2 facts total from live agents.

### The Real Data Sources

| Workspace | Path | Content | Cross-agent value |
|-----------|------|---------|-------------------|
| jasper-trader | `jasper-trader-workspace/memory/202*.md` | Trades, market scans, positions, P&L, risk status | **HIGH** — code/planner need to know trading is blocked or risk is exhausted |
| jasper-code | `jasper-code-workspace/memory/202*.md` | Commits, bug fixes, deployments, validation results | **HIGH** — trader needs to know broker adapter is fixed |
| jasper-research | `jasper-research-workspace/memory/202*.md` | Job leads, market forecasts, interview prep | **MEDIUM** — trader needs EUR/USD outlook, planner needs job leads |
| jasper-planner | `jasper-planner-workspace/memory/202*.md` | Task assignments, priorities, coordination | **MEDIUM** — all agents need current priorities |
| main/brain | `.openclaw/workspace/memory/202*.md` | Reviews, corrections, decisions, escalations | **CRITICAL** — contains human authority that must propagate |

### Design: Daily Notes Extraction Pipeline

**A. Nightly extraction job** — New `DailyNotesExtractor` (same LLM-assisted pattern as `MemoryFileExtractor` but with a cross-agent-focused prompt):

Source paths (configured, not hardcoded):
```
~/.openclaw/workspace/memory/202*.md
~/Projects/jasper-code-workspace/memory/202*.md
~/Projects/jasper-trader-workspace/memory/202*.md
~/Projects/jasper-research-workspace/memory/202*.md
~/Projects/jasper-planner-workspace/memory/202*.md
```

Extraction prompt instructs the LLM to:
- Extract only facts that **other agents would benefit from knowing**
- Skip internal details (auth tokens, build output, file paths)
- Use canonical predicates from the alias table
- Tag with source agent and date
- Flag corrections/decisions as `human_correction` source type when they originate from operator/main reviews

**B. Incremental extraction** — Track last-processed file + line offset per workspace. Only extract from new/modified files since last run. Store watermark in `mycelium.extraction_state` table.

**C. Cross-agent filtering heuristic** — Not everything in a daily note is cross-agent relevant. The extraction prompt should focus on:
- **State changes**: "risk budget exhausted", "trading pipeline blocked", "broker adapter fixed"
- **Decisions**: "skipped EUR/USD, took GBP/USD instead" (relevant for research's market analysis)
- **Discoveries**: "Alpaca API returns 429 after 15:00 UTC" (relevant for code agent)
- **Human feedback**: "agent ignored FEEDBACK.md repeatedly" (relevant for all)

NOT:
- Internal session mechanics (auth loading, env var checks)
- Build/test output
- Raw market data (prices, indicators — too volatile, belongs in trading system)

**D. Expire MEMORY.md migration facts** — The 309 migrated facts from MEMORY.md should be expired. They're a worse version of what agents already see.

### Files Changed
- New: `src/mycelium/extraction/daily_notes.py` — DailyNotesExtractor, incremental state tracking
- New: `src/mycelium/extraction/prompts.py` — Cross-agent extraction prompt
- `src/mycelium/server/app.py` — `/v1/extraction/run` endpoint for manual trigger
- New: `ops/nightly-extraction.sh` — launchd-triggered extraction script
- New: SQL migration for `mycelium.extraction_state` table

### Complexity: **L** (new pipeline, LLM-dependent, needs careful prompt engineering)

### Impact
Mycelium goes from 2 live facts to hundreds of genuinely cross-agent facts. Jasper-trader knows jasper-code fixed the broker adapter. Jasper-code knows trading is blocked. The system contains knowledge that agents **don't already have**.

---

## 0b. Human Correction Flow — The #2 Problem

### Status Quo
When the operator corrects an agent via main (Telegram), the correction lives in main's daily notes and maybe a FEEDBACK.md file for the target agent. **Nothing reaches Mycelium.** The `correct()` API exists and does exactly the right thing (expire old fact, create `human_correction` with max trust, propagate to all agents) — but nothing calls it.

Concrete example: main noted a research agent repeatedly reporting the same finding as HIGH — despite prior feedback. That correction:
- Didn't enter Mycelium
- Didn't propagate to other agents
- Didn't affect the agent's trust score
- Will likely be ignored again tomorrow

### The Correction Flow Today

```
Operator → Telegram → main agent → daily-notes + FEEDBACK.md → [DEAD END]
                                                                  ↓
                                                      Agent MAY read FEEDBACK.md
                                                      (some agents don't, repeatedly)
```

### Design: Human Correction Bridge

**A. Main agent as correction authority** — When the daily notes extractor processes main's notes (`~/.openclaw/workspace/memory/202*.md`), it identifies corrections and decisions with special handling:

Patterns that indicate corrections:
- Explicit feedback: "stærk feedback skrevet", "gentaget", "ignorerer feedback"
- Rejections: "no action taken", "unverified/irrelevant"
- Decisions: "approved", "authorized"
- Reviews with corrections: agent review sections

These are ingested as `source_type: human_correction` (trust weight 1.0) and propagated to **all** agents via Mycelium's existing correction propagation.

**B. Explicit correction from plugin** — New hook in mycelium-connector: when main agent writes a correction (detected via patterns in output or a dedicated `/correct` command), call `POST /v1/agents/main/correct` with:
- The fact being corrected (matched by subject/agent)
- The correction content
- Reason from the human

This is the real-time path. The nightly extraction is the catch-all.

**C. Trust impact** — When a human correction targets a specific agent's fact:
1. The wrong fact gets expired with `valid_until` set
2. The correction fact gets `source_type: human_correction` (trust = 1.0)
3. The source agent's `facts_contradicted` counter increments
4. Over time, agents that get corrected frequently have their trust score reduced (ties into Meta-Learning, section 6)

**D. FEEDBACK.md replacement** — Once corrections flow through Mycelium with guaranteed propagation, FEEDBACK.md becomes redundant. The correction fact is propagated to the target agent's subscription and appears in their next query. Unlike FEEDBACK.md, they can't just not read it — it's injected into their context.

### The Correction Flow After

```
Operator → Telegram → main agent → daily-notes
                                        ↓
                                nightly extraction
                                        ↓
                      Mycelium correct() [human_correction, trust=1.0]
                                        ↓
                           propagation to ALL agents
                                        ↓
                 target agent's trust score adjusted downward
```

And for real-time corrections:
```
Operator → main agent → plugin detects correction → POST /correct → immediate propagation
```

### Files Changed
- `src/mycelium/extraction/daily_notes.py` — Correction detection logic in extraction prompt
- `~/.openclaw/extensions/mycelium-connector/index.ts` — Correction detection hook + `/correct` call
- No changes to core Mycelium — `correct()` and propagation already work

### Complexity: **M**
The hard part is reliable correction detection in unstructured text. The Mycelium infrastructure is already there.

### Impact
Human authority actually propagates. When the operator says something is wrong, every agent knows. Agents that repeatedly get corrected become less trusted. FEEDBACK.md fire-and-pray is replaced by guaranteed delivery.

---

## 1. Intelligent Ranking

### Status Quo
Ranking is static: `0.5 * similarity + 0.25 * trust + 0.25 * recency`. Fixed weights in `RankingWeights` dataclass. Verification status is not a signal. Agent identity and context are ignored.

### Problem
- A trader asking about BTCUSDT gets facts ranked identically to a planner asking about project status.
- Verified facts rank the same as unverified ones.
- Facts with `conflict_status: unresolved` aren't penalized.
- Agent `active_context` is used in propagation but ignored in query.
- Facts that are never accessed don't sink — decay only happens after 90 days.

### Design

**A. Agent-aware ranking profiles** — `RankingProfile` per agent role:

```
RankingProfile {
    similarity_weight: float
    trust_weight: float
    recency_weight: float
    recency_half_life_hours: int
    verification_boost: float
    conflict_penalty: float
    stale_penalty: float
}
```

Default profiles:
- **trader**: high recency (0.35), short half-life (24h), high trust (0.30), conflict penalty 0.3
- **code**: high similarity (0.55), long half-life (720h), low recency (0.15)
- **research**: balanced, boosted verification weight
- **coordinator**: balanced defaults

**B. Verification status as ranking signal** — `verified` +0.15, `stale` -0.10, `failed` -0.25, unresolved conflict -0.10.

**C. Active context boost in query** — Load agent's `active_context`, boost facts matching current entities (0.1-0.3 depending on urgency). Reuse propagation's `_compute_context_boost` logic.

**D. Access-weighted decay** — Facts with `access_count == 0` after 14 days: -0.05 score penalty.

### Files Changed
- `src/mycelium/pipelines/query.py` — Expand `_compute_score`, add `RankingProfile`
- `src/mycelium/domain/types.py` — `RankingProfile` dataclass
- `src/mycelium/server/app.py` — Pass agent record to query engine
- `src/mycelium/client/client.py` — Accept `RankingProfile` at connect

### Complexity: **M** | Impact: Role-appropriate ranking. Trader sees recent high-confidence facts. Code agent sees stable architectural facts.

---

## 2. Fact Consolidation

### Status Quo
No consolidation. Duplicate subjects flood results. Token budget wasted on redundant facts.

### Design

**A. Subject-clustered views** — Group results by subject, return highest-scored representative per cluster.

**B. Semantic dedup at ingest** — >0.95 similarity + same subject + same predicate = corroboration, not new fact.

**C. Summary facts (deferred, LLM-assisted)** — Background job for 5+ fact clusters.

### Complexity: **M** | Impact: "10 consolidated results" instead of "50 facts about 10 subjects".

---

## 3. Proactive Relevance

### Status Quo
Propagation: topic wildcards only. No semantic matching. No urgency escalation.

### Design

**A. Semantic subscription matching** — Embedding similarity between fact and agent profile. Propagate if > 0.6 even without topic match.

**B. Priority escalation** — `human_correction` → always CRITICAL. Contradiction with high-confidence fact → ELEVATED. Entity in critical active_context → CRITICAL.

**C. Normalized entity matching** — `EUR/USD` ↔ `EURUSD`, case-fold, punctuation strip.

### Complexity: **M** | Impact: Cross-domain facts reach the right agents. Corrections interrupt immediately.

---

## 4. Feedback & Learning

### Status Quo
Plugin computes hit-rate and logs to Supabase. Data is write-only — never feeds back into ranking.

### Design

**A. Implicit feedback** — Background job reads `mycelium_metrics`, adjusts `usefulness_score` on facts.

**B. Explicit feedback API** — `POST /feedback` with signal: helpful/irrelevant/outdated/wrong.

**C. Feedback-adjusted weights** — Per-agent ranking auto-tunes based on hit-rate patterns (Phase 3).

### Complexity: **L** | Impact: System learns what's useful. Unused facts sink. Agent-specific improvement.

---

## 5. Temporal Intelligence

### Status Quo
All facts have same decay (90 days). No TTL. No version chains. No trend detection.

### Design

**A. Fact-type TTL** — Trading facts: 4h. Service status: 24h. Architecture: null. Checked by `DecayCycleRunner`.

**B. Version chains** — Same subject + same predicate + later timestamp = SUPERSEDES, not CONTRADICTS.

**C. Trend extraction (deferred)** — LLM summarizes 3+ version chains.

### Complexity: **M** | Impact: Trading facts auto-expire. Architecture facts persist. Clean version history.

---

## 6. Meta-Learning

### Status Quo
Agent trust tracked but doesn't feed back. No gap detection. No stale-topic detection.

### Design

**A. Agent reliability loop** — Periodic job computes contradiction_rate, adjusts trust. Ingested as meta-fact per spec 3.4. Combined with human correction data (section 0b), agents that get corrected by the operator frequently get trust-penalized automatically.

**B. Knowledge gap detection** — Track query misses. Flag when 3+ agents query same topic with 0 results.

**C. Stale topic detection** — Flag topics with all-stale/expired facts that are still queried.

### Complexity: **L** | Impact: Bad agents get penalized. Knowledge gaps become visible.

---

## 7. Production Hardening

### Status Quo
~~Datetime crash in production.~~ (Fixed — UTC normalization shipped in post-beta hardening.) 0 verified facts. Plugin has 2s query timeout.

### Design

**A. Fact listing endpoint** — `GET /v1/agents/{agent_id}/facts` with pagination. 404s in logs show this is needed.

**B. Structured monitoring** — Latency histograms, error rates, fact counts via `/metrics`.

**C. Embedding quality validation** — One-time benchmark of `text-embedding-3-small` precision/recall.

**D. Query latency profiling** — Timing breakdown in ops log. Verify 50ms target with pgvector HNSW.

### Complexity: **S-M** | Impact: Operators can see what's happening. Debug workflows unblocked.

---

## 8. Additional Observations

### 8.1 Plugin Never Calls `update_context`
`active_context` is never set. The spec's dynamic relevance (6.2.1) is dead in production. Fix: plugin calls `update_context` on every `before_prompt_build` with task name + entities + urgency. 5 lines of TS.

### 8.2 Verification Completely Unused
311 facts, 0 verified. Add `VerificationCycleRunner` that runs existing providers against unverified facts.

### 8.3 Predicate Quality is Terrible
140+ unique predicates from LLM extraction. "is" (52), "include" (17), "status as of 2026-03-08" (3), full sentences as predicates. The canonical predicate system exists but the extraction prompt didn't enforce it. New extraction (section 0) must include the canonical list in the prompt.

### 8.4 Connection Overhead
Plugin reconnects on every session. Add `connected_since` to response, cache for 1h.

### 8.5 MEMORY.md Migration Facts Must Be Expired
The 309 facts are a worse copy of information already in agent context. Expire all facts where `metadata->>'migrated_from' = 'memory_file'`.

---

## Prioritized Implementation Plan

### Phase 0 — Fix the Foundation (this week)

| # | Item | Area | Complexity | Why First |
|---|------|------|------------|-----------|
| 1 | Expire 309 MEMORY.md migration facts | Data Quality | S | **They're redundant noise.** |
| 2 | Daily notes extraction pipeline | Data Source | L | **Without this, Mycelium has no useful data.** |
| 3 | Human correction bridge (nightly) | Correction Flow | M | **Human authority must propagate.** |
| 4 | Plugin `update_context` call | Proactive Relevance | S | 5 lines of TS, unlocks everything. |
| 5 | Canonical predicates in extraction prompt | Data Quality | S | Prevents the 140-predicate mess from repeating. |

### Phase 1 — Make Queries Useful (1-2 weeks)

| # | Item | Area | Complexity | Depends On |
|---|------|------|------------|------------|
| 6 | Verification status in ranking | Ranking | S | — |
| 7 | Subject-clustered query results | Consolidation | S | — |
| 8 | Auto-verification cycle | Verification | M | — |
| 9 | Fact listing endpoint | Hardening | S | — |
| 10 | Real-time correction hook in plugin | Correction Flow | M | #3 |

### Phase 2 — Smarter Delivery (2-4 weeks)

| # | Item | Area | Complexity | Depends On |
|---|------|------|------------|------------|
| 11 | Agent-aware ranking profiles | Ranking | M | — |
| 12 | Active context boost in query | Ranking | S | #4 |
| 13 | Semantic subscription matching | Propagation | M | — |
| 14 | Priority escalation (corrections = CRITICAL) | Propagation | S | #3 |
| 15 | TTL per fact-type | Temporal | M | — |
| 16 | Supersede-vs-contradict logic | Temporal | M | — |
| 17 | Semantic dedup at ingest | Consolidation | S | — |
| 18 | Explicit feedback API | Feedback | M | — |
| 19 | Entity normalization | Propagation | S | — |
| 20 | Structured monitoring | Hardening | M | — |

### Phase 3 — Self-Improvement (4-8 weeks)

| # | Item | Area | Complexity | Depends On |
|---|------|------|------------|------------|
| 21 | Implicit feedback ingestion | Feedback | M | Supabase metrics |
| 22 | Agent trust recalculation | Meta-Learning | M | #3 (correction data) |
| 23 | Knowledge gap detection | Meta-Learning | L | New storage |
| 24 | Feedback-adjusted ranking | Feedback | L | #18, #21 |
| 25 | LLM-assisted summary facts | Consolidation | L | #7 |
| 26 | Trend extraction | Temporal | L | #16 |
| 27 | Embedding quality benchmark | Hardening | M | — |

---

## Design Principles

1. **Daily notes are the primary data source** — Not MEMORY.md, not `memory_store` intercepts. The valuable knowledge is in what agents wrote about what they did.
2. **Human corrections are highest authority** — When the operator says something is wrong, that propagates to all agents immediately and permanently. `source_type: human_correction` with trust = 1.0.
3. **No LLM in the critical path** — Extraction is async/nightly. Query, ranking, propagation are deterministic.
4. **Cross-agent value filter** — Only extract facts that other agents benefit from. Internal session mechanics stay in the notes.
5. **Backwards compatible** — No breaking API changes. New fields have defaults.
6. **Testable in isolation** — Unit tests with in-memory repos. No Postgres dependency for core logic.
