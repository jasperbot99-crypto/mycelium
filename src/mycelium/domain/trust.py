"""Trust and confidence scoring — ARCHITECTURE.md Section 4, Spec section 3.4.

Pure functions. No I/O, no side effects. Takes fact metadata and agent history,
returns scores.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycelium.domain.types import AgentRecord, SourceType


def compute_initial_confidence(source_type: SourceType) -> float:
    """Compute initial confidence score for a new fact at ingest time.

    Based purely on source_type hierarchy. Corroboration and verification
    adjust this later.
    """
    return source_type.trust_weight


def compute_trust_score(
    source_type: SourceType,
    agent: AgentRecord | None = None,
    corroboration_count: int = 0,
) -> float:
    """Compute trust score for a fact.

    Factors:
    - source_type hierarchy weight (primary)
    - agent trust history (if available)
    - corroboration count (diminishing returns)

    Returns a value in [0.0, 1.0].
    """
    base = source_type.trust_weight

    # Agent history adjustment: scale base by agent's own trust score
    if agent is not None:
        # Agent trust ranges [0, 1]. Blend: 70% source_type, 30% agent history.
        agent_factor = 0.7 + 0.3 * agent.trust_score
        base *= agent_factor

    # Corroboration boost: diminishing returns via logarithmic scaling
    # 0 corroborations = no boost, 1 = +0.05, 3 = +0.10, 10 = +0.15
    if corroboration_count > 0:
        import math

        boost = 0.05 * math.log2(corroboration_count + 1)
        base = min(1.0, base + boost)

    return round(min(1.0, max(0.0, base)), 4)


def compute_agent_contradiction_rate(agent: AgentRecord) -> float:
    """Compute contradiction rate for an agent.

    Used for meta-insights (spec section 3.4): agents with high contradiction
    rates get lower trust scores on future facts.
    """
    if agent.facts_contributed == 0:
        return 0.0
    return round(agent.facts_contradicted / agent.facts_contributed, 4)
