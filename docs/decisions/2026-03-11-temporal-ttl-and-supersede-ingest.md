# Decision: Temporal TTL and Supersede-First Ingest

Date: 2026-03-11

## Context
Improvement strategy Phase 2 called out two temporal gaps:
- No fact-type TTL policy.
- Newer state updates were treated as contradictions instead of version updates.

This produced stale operational facts and noisy conflict creation for normal status changes.

## Decision
1. Add fact-type TTL in `DecayCycleRunner`:
- Trading/market facts default to 4h TTL.
- Service status facts default to 24h TTL.
- Architecture/design facts get no TTL expiration.
- `metadata.ttl_hours` can override TTL per fact.

2. Add supersede-vs-contradict handling in ingest:
- Contradictions that are same subject + same predicate + changed object + newer timestamp are treated as temporal supersedes.
- In these cases, ingest creates `SUPERSEDES` relation(s), sets `valid_until` on superseded fact(s), and does not create conflict records.
- Remaining contradictions still follow normal conflict flow.

## Rationale
- Operational/trading facts should age out quickly without waiting for stale-day windows.
- Status evolution should build clean version chains, not contradiction noise.
- The approach is deterministic and keeps the critical path LLM-free.

## Consequences
- Decay cycle now reports `expired_ttl` as an explicit outcome.
- Ingest can emit supersede relations from normal updates, not only from explicit `correct()`.
- Conflict volume drops for expected temporal updates while preserving contradiction detection for true disagreements.
