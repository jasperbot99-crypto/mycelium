"""Tests for PropagationEngine — ARCHITECTURE.md Section 7."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from mycelium.domain.types import (
    ActiveContext,
    AgentRecord,
    Fact,
    FactContent,
    Priority,
    SourceType,
    Subscription,
    Urgency,
)
from mycelium.ops.logger import InMemoryOpsLogger
from mycelium.pipelines.propagation import PropagationEngine
from mycelium.storage.memory import (
    InMemoryAgentRepository,
    InMemoryEventLog,
    InMemorySubscriptionRepository,
)
from mycelium.transport.in_process import InProcessTransport


def _make_fact(
    tags: list[str] | None = None,
    source_agent_id: str = "agent-a",
    confidence: float = 0.8,
    source_type: SourceType = SourceType.AGENT_EXTRACTION,
) -> Fact:
    return Fact(
        id=uuid4(),
        content=FactContent(subject="api-orders", predicate="has_status", object="healthy"),
        source_agent_id=source_agent_id,
        source_type=source_type,
        confidence=confidence,
        trust_score=0.7,
        valid_from=datetime.now(),
        tags=tags or [],
    )


def _make_subscription(
    agent_id: str = "agent-b",
    topic: str = "api.*",
    priority: Priority = Priority.HIGH,
    min_confidence: float = 0.0,
    source_types: list[SourceType] | None = None,
) -> Subscription:
    return Subscription(
        id=uuid4(),
        agent_id=agent_id,
        topic=topic,
        priority=priority,
        min_confidence=min_confidence,
        source_types=source_types,
    )


@pytest.fixture
def sub_repo() -> InMemorySubscriptionRepository:
    return InMemorySubscriptionRepository()


@pytest.fixture
def agent_repo() -> InMemoryAgentRepository:
    return InMemoryAgentRepository()


@pytest.fixture
def event_log() -> InMemoryEventLog:
    return InMemoryEventLog()


@pytest.fixture
def transport() -> InProcessTransport:
    return InProcessTransport()


@pytest.fixture
def ops_logger() -> InMemoryOpsLogger:
    return InMemoryOpsLogger()


@pytest.fixture
def engine(
    sub_repo: InMemorySubscriptionRepository,
    agent_repo: InMemoryAgentRepository,
    event_log: InMemoryEventLog,
    transport: InProcessTransport,
    ops_logger: InMemoryOpsLogger,
) -> PropagationEngine:
    return PropagationEngine(
        subscription_repo=sub_repo,
        event_log=event_log,
        transport=transport,
        agent_repo=agent_repo,
        ops_logger=ops_logger,
    )


class TestPropagationEngine:
    @pytest.mark.asyncio
    async def test_propagates_to_matching_subscription(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
        event_log: InMemoryEventLog,
    ) -> None:
        sub = _make_subscription(agent_id="agent-b", topic="api.*")
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=["api.orders"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 1
        assert events[0].target_agent_id == "agent-b"
        assert events[0].fact_id == fact.id
        assert events[0].reason == "subscription:api.*"
        assert events[0].priority == Priority.HIGH

    @pytest.mark.asyncio
    async def test_does_not_propagate_to_source_agent(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
    ) -> None:
        """Source agent should not receive its own facts."""
        sub = _make_subscription(agent_id="agent-a", topic="*")
        await sub_repo.sync_subscriptions("agent-a", [sub])

        fact = _make_fact(tags=["api.orders"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_does_not_propagate_when_tags_dont_match(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
    ) -> None:
        sub = _make_subscription(agent_id="agent-b", topic="database.*")
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=["api.orders"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_wildcard_star_matches_all_tags(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
    ) -> None:
        sub = _make_subscription(agent_id="agent-b", topic="*")
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=["anything"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_filters_by_min_confidence(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
    ) -> None:
        sub = _make_subscription(agent_id="agent-b", topic="*", min_confidence=0.9)
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=["api"], confidence=0.5)
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_filters_by_source_type(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
    ) -> None:
        sub = _make_subscription(
            agent_id="agent-b",
            topic="*",
            source_types=[SourceType.HUMAN_CORRECTION],
        )
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=["api"], source_type=SourceType.AGENT_EXTRACTION)
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_multiple_agents_receive_events(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
    ) -> None:
        sub_b = _make_subscription(agent_id="agent-b", topic="api.*")
        sub_c = _make_subscription(agent_id="agent-c", topic="api.*")
        await sub_repo.sync_subscriptions("agent-b", [sub_b])
        await sub_repo.sync_subscriptions("agent-c", [sub_c])

        fact = _make_fact(tags=["api.orders"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 2
        agents = {e.target_agent_id for e in events}
        assert agents == {"agent-b", "agent-c"}

    @pytest.mark.asyncio
    async def test_events_appended_to_event_log(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
        event_log: InMemoryEventLog,
    ) -> None:
        sub = _make_subscription(agent_id="agent-b", topic="*")
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=["api"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 1
        assert events[0].sequence_num > 0

        # Event should be in the log
        logged = await event_log.get_since("agent-b")
        assert len(logged) == 1

    @pytest.mark.asyncio
    async def test_engine_does_not_mark_delivered(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
        event_log: InMemoryEventLog,
        transport: InProcessTransport,
    ) -> None:
        """Engine publishes but doesn't mark delivered — client's _handle_event does that."""
        received: list[object] = []
        await transport.subscribe("agent-b", lambda e: _async_append(received, e))

        sub = _make_subscription(agent_id="agent-b", topic="*")
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=["api"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 1
        assert len(received) == 1
        # Engine does not mark delivered
        assert events[0].delivered is False
        undelivered = await event_log.get_undelivered("agent-b")
        assert len(undelivered) == 1

    @pytest.mark.asyncio
    async def test_undelivered_when_no_listener(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
        event_log: InMemoryEventLog,
    ) -> None:
        """When no transport listener, event stays undelivered for replay."""
        sub = _make_subscription(agent_id="agent-b", topic="*")
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=["api"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 1
        assert events[0].delivered is False
        undelivered = await event_log.get_undelivered("agent-b")
        assert len(undelivered) == 1

    @pytest.mark.asyncio
    async def test_ops_logging(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
        ops_logger: InMemoryOpsLogger,
    ) -> None:
        sub = _make_subscription(agent_id="agent-b", topic="*")
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=["api"], source_agent_id="agent-a")
        await engine.propagate(fact, "agent-a")

        prop_logs = await ops_logger.find_by_operation("propagation")
        assert len(prop_logs) == 1
        assert prop_logs[0].status == "success"
        assert prop_logs[0].detail is not None
        assert prop_logs[0].detail["events_created"] == 1

    @pytest.mark.asyncio
    async def test_no_events_when_no_subscriptions(
        self,
        engine: PropagationEngine,
    ) -> None:
        fact = _make_fact(tags=["api"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_no_events_when_fact_has_no_tags(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
    ) -> None:
        """Fact with no tags won't match topic subscriptions (except '*')."""
        sub = _make_subscription(agent_id="agent-b", topic="api.*")
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=[], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_wildcard_matches_fact_with_no_tags(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
    ) -> None:
        """'*' subscription matches even facts with no tags."""
        sub = _make_subscription(agent_id="agent-b", topic="*")
        await sub_repo.sync_subscriptions("agent-b", [sub])

        fact = _make_fact(tags=[], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_context_entity_match_added_to_reason(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
        agent_repo: InMemoryAgentRepository,
    ) -> None:
        sub = _make_subscription(agent_id="agent-b", topic="api.*")
        await sub_repo.sync_subscriptions("agent-b", [sub])
        agent = await agent_repo.upsert(
            AgentRecord(
                id="agent-b",
                role="tester",
                active_context=ActiveContext(
                    entities=("api-orders",),
                    urgency=Urgency.ELEVATED,
                ),
            )
        )
        assert agent.id == "agent-b"

        fact = _make_fact(tags=["api.orders"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 1
        assert "context:entity_match:elevated" in events[0].reason
        assert "score:" in events[0].reason

    @pytest.mark.asyncio
    async def test_no_context_reason_when_entities_do_not_match(
        self,
        engine: PropagationEngine,
        sub_repo: InMemorySubscriptionRepository,
        agent_repo: InMemoryAgentRepository,
    ) -> None:
        sub = _make_subscription(agent_id="agent-b", topic="api.*")
        await sub_repo.sync_subscriptions("agent-b", [sub])
        await agent_repo.upsert(
            AgentRecord(
                id="agent-b",
                role="tester",
                active_context=ActiveContext(
                    entities=("database-main",),
                    urgency=Urgency.CRITICAL,
                ),
            )
        )

        fact = _make_fact(tags=["api.orders"], source_agent_id="agent-a")
        events = await engine.propagate(fact, "agent-a")

        assert len(events) == 1
        assert events[0].reason == "subscription:api.*"


async def _async_append(lst: list[object], item: object) -> None:
    lst.append(item)
