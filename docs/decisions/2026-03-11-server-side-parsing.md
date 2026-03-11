# Decision: Move Parsing Logic from Plugin to Server

Date: 2026-03-11

## Context

The mycelium-connector OpenClaw plugin (TypeScript, ~650 lines) contained ~200 lines of parsing and extraction logic: subject extraction, garbage detection, memory_store format parsing, and entity extraction for queries. This created three problems:

1. **Inconsistent parsing across clients** — any new client (CLI, web UI, other plugins) would need to reimplement the same logic.
2. **Deployment coupling** — changing parsing heuristics required redeploying the plugin to every OpenClaw instance.
3. **Oversized plugin** — the plugin mixed transport concerns (HTTP bridge, metrics, formatting) with domain logic (what constitutes a valid fact).

## Decision

Move all parsing/extraction logic to the Mycelium Python server as `mycelium.pipelines.parsing`, a pure-function module with no I/O. Expose it through two new server capabilities:

1. **`POST /v1/agents/{id}/ingest/raw`** — accepts raw text, server parses it into a structured fact and feeds it into the existing ingest pipeline. Returns parse rejection if input is garbage or unparseable.
2. **`raw_context` field on `QueryRequest`** — when `question` is empty and `raw_context` is provided, the server extracts entities from task name and prompt to build the query. Existing clients sending `question` directly are unaffected.

Additionally, the hallucination pipeline gains a 5th check: if `content.object` looks like garbage (JSON fragments, credentials), it gets penalty 1.0 (auto-reject). This protects the structured `/ingest` endpoint too, not just `/ingest/raw`.

## Functions Ported

| Function | Purpose |
|----------|---------|
| `looks_like_garbage(text)` | Reject JSON fragments, escaped strings, high-density JSON chars, credential patterns |
| `extract_subject(content)` | 6-strategy cascade: tickers, backtick code, quoted strings, proper nouns, noun-before-verb, fallback |
| `parse_memory_store_to_fact(input, agent_id)` | Parse `[agent] [type] content` format into `ParsedFact` |
| `extract_entities(text, known?)` | Known names, capitalized phrases, quoted strings, hyphenated identifiers (max 8) |
| `extract_query_from_context(task?, prompt?)` | Combine task name with extracted entities |

## Files Changed

| File | Action |
|------|--------|
| `src/mycelium/pipelines/parsing.py` | NEW — all 5 parsing functions |
| `src/mycelium/server/dto.py` | EXTENDED — `RawIngestRequest`, `RawIngestResponse`, `RawQueryContext`; `QueryRequest.question` defaults to `""` |
| `src/mycelium/server/app.py` | EXTENDED — `/ingest/raw` endpoint, query supports `raw_context` |
| `src/mycelium/pipelines/hallucination.py` | EXTENDED — check #5 `garbage_content` (penalty 1.0) |
| `tests/unit/test_parsing.py` | NEW — 45 unit tests |
| `~/.openclaw/extensions/mycelium-connector/index.ts` | SIMPLIFIED — v0.3.0 → v0.4.0, removed ~200 lines of parsing, plugin now ~310 lines |

## Backward Compatibility

- Existing `/ingest` endpoint unchanged.
- `QueryRequest.question` now defaults to `""` instead of being required. Existing clients always send it, so no breaking change.
- Plugin v0.4.0 requires Mycelium server with `/ingest/raw` support. Older servers will 404, which the plugin handles gracefully (fire-and-forget ingest).

## What Stays in the Plugin

- `formatFactsForPrompt` — prompt formatting is a client/UX concern
- `estimateTokens` — token budgeting is a client concern
- `AGENT_ROLES` / `AGENT_SUBSCRIPTIONS` — agent configuration
- `MYCELIUM_TOKEN_BUDGET` — per-session-type budgets
- Trade rate-limiting — client-side throttle
- Supabase metrics logging — observability is separate from Mycelium (spec 5.5)
- Session hit-rate scoring — behavioral metrics
