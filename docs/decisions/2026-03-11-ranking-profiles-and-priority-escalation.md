# Decision: Role-Aware Ranking and Propagation Priority Escalation

Date: 2026-03-11

## Context
The improvement strategy calls out two remaining quality gaps:
- Query ranking used static weights for every agent role.
- Propagation priority did not escalate for human corrections, high-confidence conflicts, or critical active context.

This caused trader/code/research agents to get similarly ranked results despite very different recency needs, and it weakened urgent cross-agent delivery.

## Decision
1. Add role-aware ranking profiles in query:
- Introduce `RankingProfile` as a domain type.
- Use role defaults for `trader`, `code`, `research`, and `coordinator`.
- Keep backward compatibility by deriving default profile from legacy `RankingWeights` when role is unknown.
- Support explicit profile override from `MyceliumClient`.

2. Add access-weighted stale penalty:
- Apply a small score penalty to facts with `access_count == 0` after a grace period.
- This gently sinks old unread facts without mutating stored trust/confidence.

3. Escalate propagation priority deterministically:
- `human_correction` facts are always escalated to `CRITICAL`.
- Unresolved high-confidence conflicts escalate to at least `HIGH`.
- Entity match in `CRITICAL` active context escalates to `CRITICAL`.
- Event reason now includes escalation marker for observability.

4. Normalize context entity matching:
- Add punctuation-insensitive normalization so entities like `EUR/USD` match `EURUSD`.

## Rationale
- Role-aware profiles align ranking with agent job-to-be-done.
- Access penalty addresses persistent unused fact clutter.
- Escalation rules enforce human authority and urgency semantics without introducing LLM dependence.
- Normalized matching improves practical recall for finance/infrastructure naming variants.

## Consequences
- Query API remains backward compatible; new role/profile inputs are optional.
- Client query path now passes agent role and optional ranking profile to QueryEngine.
- Propagation event priorities can now exceed subscription base priority when escalation conditions are met.
- Additional unit tests cover ranking profile behavior, stale unread penalty, escalation paths, and entity normalization.
