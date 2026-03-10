"""Propagation engine — ARCHITECTURE.md Section 7.

After ingest, evaluates all subscriptions against the new fact.
For each match: creates a PropagationEvent, appends to EventLog, publishes via Transport.

Error contract (ARCHITECTURE.md Section 7.2):
- Transport failures are logged but never block the ingest pipeline.
- Failed deliveries stay as undelivered in EventLog for replay.
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from mycelium.domain.types import ActiveContext, Fact, Priority, PropagationEvent, Urgency
from mycelium.ops.logger import NullOpsLogger, OpsLogger, ms_since
from mycelium.storage.protocols import AgentRepository, EventLog, SubscriptionRepository
from mycelium.transport.protocols import Transport

logger = logging.getLogger(__name__)


_PRIORITY_WEIGHT: dict[Priority, float] = {
    Priority.LOW: 0.25,
    Priority.NORMAL: 0.5,
    Priority.HIGH: 0.75,
    Priority.CRITICAL: 1.0,
}

_URGENCY_MULTIPLIER: dict[Urgency, float] = {
    Urgency.NORMAL: 1.0,
    Urgency.ELEVATED: 1.2,
    Urgency.CRITICAL: 1.4,
}


class PropagationEngine:
    """Evaluate subscriptions and propagate facts to matching agents."""

    def __init__(
        self,
        subscription_repo: SubscriptionRepository,
        event_log: EventLog,
        transport: Transport,
        agent_repo: AgentRepository | None = None,
        ops_logger: OpsLogger | None = None,
    ) -> None:
        self._sub_repo = subscription_repo
        self._event_log = event_log
        self._transport = transport
        self._agent_repo = agent_repo
        self._ops = ops_logger or NullOpsLogger()

    async def propagate(self, fact: Fact, source_agent_id: str) -> list[PropagationEvent]:
        """Evaluate all subscriptions and propagate fact to matching agents.

        Returns the list of created PropagationEvents (one per matching subscription).
        Does not propagate back to the source agent.
        """
        t0 = time.monotonic()
        events: list[PropagationEvent] = []
        relevance_scores: list[float] = []
        context_matched_count = 0

        subscriptions = await self._sub_repo.get_all()
        agent_context_cache: dict[str, ActiveContext | None] = {}

        for sub in subscriptions:
            # Don't propagate back to the agent that ingested the fact
            if sub.agent_id == source_agent_id:
                continue

            if not sub.matches_tags(fact.tags):
                continue

            if not sub.passes_filters(fact):
                continue

            context = await self._get_active_context(sub.agent_id, agent_context_cache)
            context_boost, context_reason = self._compute_context_boost(fact, context)
            relevance_score = _PRIORITY_WEIGHT[sub.priority] * (1.0 + context_boost)
            relevance_scores.append(relevance_score)
            if context_boost > 0:
                context_matched_count += 1

            reason = f"subscription:{sub.topic}"
            if context_reason is not None:
                reason += f"|context:{context_reason}|score:{relevance_score:.2f}"

            # Create propagation event
            event = PropagationEvent(
                id=uuid4(),
                fact_id=fact.id,
                target_agent_id=sub.agent_id,
                reason=reason,
                priority=sub.priority,
            )

            # Append to event log (gets sequence_num assigned)
            event = await self._event_log.append(event)

            # Publish via transport (fire-and-forget semantics).
            # Delivery tracking is handled by the client's _handle_event callback,
            # which marks the event as delivered after the on_fact callback succeeds.
            try:
                await self._transport.publish(sub.agent_id, event)
            except Exception:
                logger.exception(
                    "propagation_publish_failed: agent=%s event=%s fact=%s",
                    sub.agent_id,
                    event.id,
                    fact.id,
                )

            events.append(event)

        await self._ops.log(
            "propagation",
            "success",
            fact_id=fact.id,
            latency_ms=ms_since(t0),
            detail={
                "source_agent": source_agent_id,
                "events_created": len(events),
                "context_matches": context_matched_count,
                "avg_relevance": (
                    round(sum(relevance_scores) / len(relevance_scores), 4)
                    if relevance_scores
                    else 0.0
                ),
            },
        )

        return events

    async def _get_active_context(
        self,
        agent_id: str,
        cache: dict[str, ActiveContext | None],
    ) -> ActiveContext | None:
        if agent_id in cache:
            return cache[agent_id]
        if self._agent_repo is None:
            cache[agent_id] = None
            return None
        agent = await self._agent_repo.get_by_id(agent_id)
        context = agent.active_context if agent is not None else None
        cache[agent_id] = context
        return context

    def _compute_context_boost(
        self,
        fact: Fact,
        context: ActiveContext | None,
    ) -> tuple[float, str | None]:
        if context is None:
            return 0.0, None

        subject = fact.content.subject.lower()
        tags = {tag.lower() for tag in fact.tags}

        entity_match = False
        for entity in context.entities:
            needle = entity.strip().lower()
            if not needle:
                continue
            if needle == subject:
                entity_match = True
                break
            if needle in subject or any(needle in tag for tag in tags):
                entity_match = True
                break

        if not entity_match:
            return 0.0, None

        urgency_multiplier = _URGENCY_MULTIPLIER[context.urgency]
        boost = 0.5 * urgency_multiplier
        return round(boost, 2), f"entity_match:{context.urgency.value}"
