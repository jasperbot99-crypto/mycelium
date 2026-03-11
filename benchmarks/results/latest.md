# Mycelium Benchmark Report

| Metric | Current | Baseline |
|---|---:|---:|
| ingest_p95_ms | 0.073 | 100.0 |
| query_p95_ms | 0.157 | 50.0 |
| propagation_p95_ms | 0.028 | 1000.0 |
| conflict_resolution_per_s | 20000.0 | 10.0 |

## Spec Targets

- ingest <100ms p95: PASS
- query <50ms p95: PASS
- propagation <1000ms p95: PASS