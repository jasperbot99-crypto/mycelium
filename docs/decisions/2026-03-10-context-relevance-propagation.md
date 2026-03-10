# Decision: Context-Aware Propagation Without Dropping Static Matches

_Date: 2026-03-10_  
_Status: Accepted_

## Context

Phase 2 required active context and dynamic relevance matching. Existing propagation only used static tag subscriptions and filters, so context had no effect.

ARCHITECTURE and SPEC require:
- Static subscriptions remain baseline routing.
- Active context boosts relevance for what an agent is currently working on.
- Agents that don't update context should still receive normal propagations.

## Decision

Keep static subscription matching as the gate, then add context-derived relevance metadata:

1. Evaluate static subscription match and filters first.
2. Fetch target agent `active_context` (if available).
3. Apply context boost only when context entities match fact subject/tags.
4. Preserve event delivery for static matches even when there is no context match.
5. Encode context match and computed score in `PropagationEvent.reason`.

## Rationale

- Avoids regressions where stale/missing context causes missed facts.
- Satisfies dynamic relevance requirement without introducing hard rejection thresholds yet.
- Keeps event priority semantics stable while still exposing relevance signals for later tuning.

## Implementation Notes

- `PropagationEngine` now accepts `AgentRepository` and computes relevance score:
  - `priority_weight * (1 + context_boost)`
- Context boost factors:
  - Entity match (subject/tag) enables boost.
  - Urgency multiplier (`normal`, `elevated`, `critical`) scales boost.
- `MyceliumClient.connect()` now wires `agent_repo` into `PropagationEngine`.
- Added propagation tests for:
  - Context entity match reflected in event reason.
  - No context annotation when entities do not match.

## Next Steps

- Introduce configurable relevance thresholds once operational data exists.
- Feed relevance score into delivery priority escalation if needed.
