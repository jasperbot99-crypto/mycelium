# Mycelium Benchmark Report

| Metric | Current | Baseline |
|---|---:|---:|
| ingest_p95_ms | 0.467 | 100.0 |
| query_p95_ms | 1.357 | 50.0 |
| propagation_p95_ms | 0.553 | 1000.0 |
| conflict_resolution_per_s | 2872.531 | 10.0 |

## Spec Targets

- ingest <100ms p95: PASS
- query <50ms p95: PASS
- propagation <1000ms p95: PASS