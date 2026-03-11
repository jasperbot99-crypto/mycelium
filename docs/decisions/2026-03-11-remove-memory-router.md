# Decision: Remove standalone MemoryRouter

Date: 2026-03-11

## Context

`src/mycelium/router.py` contained a `MemoryRouter` class with `route_fact()` that matched facts against subscriptions (`matches_tags`, `passes_filters`) and deduplicated by agent ID. This was an early Phase 1 abstraction.

When the propagation pipeline was implemented in Phase 2, the same logic was built directly into `src/mycelium/pipelines/propagation.py` as part of the `PropagationEngine`. The two implementations were redundant — `MemoryRouter` was no longer used by any production code path.

## Decision

Remove `router.py` and its corresponding tests in `test_phase1_core.py`. Subscription matching and routing live in the propagation pipeline, which is where SPEC 6.4 and ARCHITECTURE place them.

## Consequences

- No behavioral change — `MemoryRouter` was dead code.
- Routing logic has a single home in `propagation.py`, reducing confusion about which module is authoritative.
- `test_phase1_core.py` retains `TestSupabaseMemoryStore` tests; only the router tests were removed.
