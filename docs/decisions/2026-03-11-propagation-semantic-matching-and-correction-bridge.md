# Decision: Semantic Propagation Matching and Safer Real-Time Correction Bridge

Date: 2026-03-11

## Context
Topic wildcard subscriptions alone miss relevant cross-domain facts. At the same time, real-time correction ingestion from the OpenClaw connector needed stricter routing to avoid accidental dual-path ingest.

## Decision
1. Add semantic subscription fallback in propagation:
- If tag/topic matching fails, propagation now evaluates semantic similarity between fact content and subscription topic.
- Propagation proceeds when semantic similarity is above threshold (default `0.6`).
- Semantic matches are annotated in event reason (`semantic:<score>`).

2. Keep correction ingestion explicit and safe in connector flow:
- `main` correction commands use explicit formats (`/correct ...` and structured `CORRECTION:` blocks).
- Correction commands are routed to `/correct` and no longer double-routed to `/ingest/raw`.

3. Add operational introspection endpoint:
- `GET /v1/agents/{agent_id}/facts` with pagination and active-only filtering.

## Rationale
- Semantic fallback improves delivery quality when tagging is incomplete.
- Explicit correction syntax reduces false positives while still enabling real-time human authority flow.
- Fact listing endpoint removes observability blind spots seen during ops/debugging.

## Consequences
- Propagation now optionally depends on embedding provider for semantic fallback.
- Mycelium client wiring passes embedding provider into propagation engine.
- Additional correction metrics (`correction_count`, `correction_errors`) are logged by connector.
