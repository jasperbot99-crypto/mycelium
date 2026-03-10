# Decision: Phase 4 Conflict Resolution, Causal Provenance, and Consistency

_Date: 2026-03-10_  
_Status: Accepted_

## Context

Phase 4 requires three capabilities:

1. LLM-assisted resolution for ambiguous conflicts.
2. Causal provenance chains for traceability.
3. Distributed consistency semantics for multi-process writes.

Earlier phases detected conflicts but did not provide a complete resolution workflow for ambiguous cases.

## Decision

Implement Phase 4 as three composable additions in Layer 3 + domain utilities:

1. `ConflictResolutionPipeline` for deterministic + LLM-assisted conflict resolution.
2. `ProvenancePipeline` for causal ancestor traversal.
3. Version-vector consistency helpers in `mycelium.domain.consistency`, with ingest-time vector stamping.

## Implementation Summary

- Added deterministic conflict winner selection order:
  1. Source authority (`SourceType.trust_weight`)
  2. Causal ordering (`version_vector` compare)
  3. Temporal ordering (outside ambiguity window)
- Ambiguous conflicts route to optional LLM resolver protocol.
- Added concrete LLM resolvers for `openai` and `anthropic` providers.
- LLM output is confidence-gated; low-confidence outcomes escalate.
- Resolved conflicts update conflict record status and create `SUPERSEDES` relation winner -> loser.
- Added provenance chain traversal over `derived_from`, `supersedes`, and causal relation edges.
- Added version-vector metadata assignment in ingest (`version_vector`, `origin_agent_id`, `causal_timestamp`).

## Rationale

- Keeps core operations deterministic by default.
- Preserves layered boundaries: client -> pipelines -> domain/storage.
- Makes ambiguous resolution pluggable without binding core logic to a specific LLM vendor.
- Provides explicit causal ordering needed for cross-process consistency analysis.

## Consequences

- Conflict resolution is now executable as an explicit workflow via client methods.
- Facts gain causal metadata that can be used by future server mode and replay compaction.
- Provenance tracing is available through API without direct database access.
