# Decision: Phase 5 Server + TypeScript SDK Beta Shape

Date: 2026-03-10

## Context

Phase 5 requires open-source/generalized delivery beyond Python library mode:

- server mode with API
- agent-framework agnostic SDK (Python + TypeScript)
- documentation/examples/benchmarks suitable for public beta

## Decision

1. Server mode is implemented with FastAPI and REST under `/v1/*`.
2. Server auth is single shared Bearer API key for beta (`MYCELIUM_SERVER_API_KEY`).
3. TypeScript SDK is HTTP-first and talks only to server mode (no direct DB access).
4. Core logic remains in existing Python library; server is a transport layer over `MyceliumClient`.
5. Benchmark suite is reproducible and outputs both JSON and markdown reports with baseline comparison.

## Consequences

- Public beta can integrate from both Python and TypeScript quickly.
- Auth model is simple for beta but may evolve to per-agent credentials in later phases.
- Domain model stays dataclass-based; pydantic is limited to server transport DTOs.
