# Decision: Migration CLI + TokenProvider Abstraction

Date: 2026-03-11

## Context

Migration support existed as import helpers but lacked a production-friendly operator path.
Auth for OpenAI/Anthropic integrations depended on static API keys and could not plug into OAuth refresh flows.

## Decision

1. Add a migration CLI (`mycelium-migrate`) with:
   - `apply-schema` for SQL bootstrap
   - `run` for ordered source migration execution
   - `--dry-run` and `--stop-on-error` controls
2. Add a token abstraction (`TokenProvider`) and use it across:
   - OpenAI embeddings
   - OpenAI/Anthropic conflict resolution
   - OpenAI memory-file extraction
3. Keep backward compatibility:
   - Existing `api_key` params and env-var behavior continue to work.

## Consequences

- Migration operations are now reproducible and scriptable without custom one-off scripts.
- Existing key-based deployments continue unchanged.
- OAuth/token-refresh can be integrated by injecting a custom provider without rewriting pipelines.
