# Mycelium

**Multi-agent memory and coordination system** — the missing coordination layer between AI agents.

> Making multi-agent systems that actually get smarter together, not just individually.

## What This Is

A standalone framework that gives multi-agent systems a shared nervous system for memory and coordination. Not a plugin. Not a wrapper around a vector database. A new primitive for how AI agents remember, learn from each other, and stay aligned.

## Status

🚧 **Spec phase** — See [SPEC.md](./SPEC.md) for full specification.

Research basis: [AGENT_MEMORY_RESEARCH.md](./AGENT_MEMORY_RESEARCH.md)

## Key Capabilities

- **Cross-agent knowledge propagation** — event-driven push based on relevance subscriptions
- **Temporal knowledge graph** — facts with validity windows, provenance chains, confidence scores
- **Conflict detection and resolution** — deterministic for simple cases, LLM-assisted for complex
- **Trust and quality model** — human corrections > verified state > agent extraction > inference
- **Verification layer** — facts checked against ground truth before admission

## Stack

- Python (primary), TypeScript (secondary SDK)
- PostgreSQL + pgvector via Supabase
- Supabase Realtime + Postgres event log for propagation transport

## License

TBD
