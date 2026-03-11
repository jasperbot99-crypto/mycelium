# Decision: UTC Normalization + Ops Baseline Hardening

Date: 2026-03-11

## Context

GAP analysis found a blocking runtime bug from mixing naive and timezone-aware datetimes. It also identified missing operational baseline artifacts (nightly memory extraction scheduling, healthcheck monitoring, and log rotation) and no explicit plugin->server->DB end-to-end integration test.

## Decision

1. Normalize runtime timestamps to timezone-aware UTC across the codebase.
2. Keep temporal defaults timezone-aware by replacing dataclass `default_factory=datetime.now` with UTC-aware defaults.
3. Add integration coverage for plugin-style HTTP ingestion (`/ingest/raw`) through server pipelines into Postgres-backed query.
4. Add concrete ops artifacts for launchd scheduling, health checks, and log retention.

## Consequences

- Query/decay/sweeper no longer crash on naive-vs-aware comparisons.
- Temporal model is consistent with Postgres `timestamptz` expectations.
- Production operators now have runnable templates for nightly extraction, health checks, and log cleanup.
- Integration confidence improves with an explicit server-path integration test.

## Artifacts

- `tests/integration/test_server_e2e.py`
- `scripts/nightly_memory_extraction.sh`
- `scripts/healthcheck_server.sh`
- `scripts/rotate_server_logs.sh`
- `ops/launchd/*.plist.example`
- `ops/newsyslog/mycelium.conf.example`
- `docs/OPERATIONS.md`
