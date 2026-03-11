# Decision: Verification Cycle Runner and Semantic Ingest Dedup

Date: 2026-03-11

## Context
Two strategy items were still open:
- Automated verification of unverified facts.
- Semantic dedup at ingest to avoid duplicate fact rows.

## Decision
1. Add `VerificationCycleRunner`:
- Periodically scans active facts.
- Runs pluggable providers on `unverified` facts.
- Aggregates outcomes deterministically (`failed` > `stale` > `verified`).
- Applies updates through existing `VerificationPipeline`.
- Wired into server startup/shutdown lifecycle.

2. Add semantic dedup in `IngestPipeline`:
- When a new fact is a high-similarity corroboration and matches subject/predicate/object, ingest is deduplicated.
- Existing fact’s corroboration count is incremented.
- No new fact is stored.
- Ingest returns rejection code `duplicate` with `existing_fact_id` and corroboration reference.

## Rationale
- Verification status should evolve without requiring manual endpoint calls.
- Duplicate claims should corroborate existing memory rather than inflate storage and ranking noise.

## Consequences
- Server now runs a third background runner (verification).
- Ingest behavior for exact semantic duplicates changed from “accepted new fact” to “deduplicated corroboration.”
