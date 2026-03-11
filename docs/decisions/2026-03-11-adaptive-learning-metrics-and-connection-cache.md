# Decision: Adaptive Learning, Monitoring Metrics, and Connector Connection Cache

Date: 2026-03-11

## Context
Improvement strategy follow-up still required implementation for:
- connector `connected_since` cache behavior,
- `/metrics` monitoring endpoint,
- implicit feedback ingestion,
- agent reliability meta-learning,
- knowledge-gap/stale-topic detection,
- feedback-driven ranking auto-tuning,
- summary/trend extraction,
- embedding quality benchmark.

## Decision
1. Extend server connect contract with `connected_since` and expose it in SDK types.
2. Add unauthenticated `/metrics` endpoint with Prometheus-style gauges for fact/agent/conflict counts and query latency/error health.
3. Introduce `AdaptiveLearningRunner` as a periodic background runner in server mode:
- reads behavioral signals from `mycelium_metrics` (when present),
- applies implicit feedback to fact confidence + `metadata.usefulness_score`,
- updates agent trust via contradiction-rate reliability loop,
- detects multi-agent query misses and emits gap/stale-topic meta-facts,
- auto-tunes per-agent ranking profile deltas in `agent.metadata.ranking_adjustment`,
- emits trend/summary meta-facts from repeated subject/predicate updates.
4. Apply ranking adjustments at query time in `MyceliumClient` + `QueryEngine`.
5. Add one-shot embedding retrieval quality benchmark script.
6. Update external connector plugin to cache connect for 1 hour based on `connected_since` and retry connect when query reports "not connected".

## Consequences
- Improves production observability and reduces connector reconnect churn.
- Enables self-improving ranking and operational learning without introducing LLM dependencies in the query path.
- Keeps behavior backward compatible: new fields and endpoints are additive.
