"""Memory routing primitives (Phase 1).

Routes facts to target agents based on subscription matching.
"""

from __future__ import annotations

from dataclasses import dataclass

from mycelium.domain.types import Fact, Subscription


@dataclass(slots=True)
class RoutingDecision:
    """Result for one routed target agent."""

    agent_id: str
    reason: str


class MemoryRouter:
    """In-process router for fact propagation targets.

    This router is intentionally deterministic and simple for Phase 1.
    It relies on Subscription.matches_tags + passes_filters semantics.
    """

    def route_fact(self, fact: Fact, subscriptions: list[Subscription]) -> list[RoutingDecision]:
        decisions: list[RoutingDecision] = []

        for sub in subscriptions:
            if not sub.matches_tags(fact.tags):
                continue
            if not sub.passes_filters(fact):
                continue
            decisions.append(
                RoutingDecision(
                    agent_id=sub.agent_id,
                    reason=f"topic={sub.topic},priority={sub.priority.value}",
                )
            )

        # de-duplicate by agent_id, keep first deterministic match
        seen: set[str] = set()
        unique: list[RoutingDecision] = []
        for decision in decisions:
            if decision.agent_id in seen:
                continue
            seen.add(decision.agent_id)
            unique.append(decision)

        return unique
