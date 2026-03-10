"""Tests for MyceliumClient propagation — Phase 2 integration."""

from __future__ import annotations

import pytest

from mycelium.client.client import MyceliumClient
from mycelium.config import MyceliumConfig, SubscriptionConfig
from mycelium.domain.types import (
    FactContent,
    PropagationEvent,
    SourceType,
)
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.ops.logger import InMemoryOpsLogger
from mycelium.storage.memory import (
    InMemoryAgentRepository,
    InMemoryConflictRepository,
    InMemoryEventLog,
    InMemoryFactRepository,
    InMemoryRelationRepository,
    InMemorySubscriptionRepository,
)
from mycelium.transport.in_process import InProcessTransport


@pytest.fixture
def embedding() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(dimension=64)


@pytest.fixture
def fact_repo() -> InMemoryFactRepository:
    return InMemoryFactRepository()


@pytest.fixture
def agent_repo() -> InMemoryAgentRepository:
    return InMemoryAgentRepository()


@pytest.fixture
def sub_repo() -> InMemorySubscriptionRepository:
    return InMemorySubscriptionRepository()


@pytest.fixture
def event_log() -> InMemoryEventLog:
    return InMemoryEventLog()


@pytest.fixture
def transport() -> InProcessTransport:
    return InProcessTransport()


@pytest.fixture
def ops_logger() -> InMemoryOpsLogger:
    return InMemoryOpsLogger()


def _make_client(
    agent_id: str,
    *,
    fact_repo: InMemoryFactRepository,
    agent_repo: InMemoryAgentRepository,
    sub_repo: InMemorySubscriptionRepository,
    event_log: InMemoryEventLog,
    transport: InProcessTransport,
    embedding: MockEmbeddingProvider,
    ops_logger: InMemoryOpsLogger,
    subscriptions: list[SubscriptionConfig] | None = None,
) -> MyceliumClient:
    config = MyceliumConfig(
        subscriptions=subscriptions or [],
    )
    return MyceliumClient(
        agent_id=agent_id,
        config=config,
        role="tester",
        fact_repo=fact_repo,
        agent_repo=agent_repo,
        conflict_repo=InMemoryConflictRepository(),
        relation_repo=InMemoryRelationRepository(),
        subscription_repo=sub_repo,
        event_log=event_log,
        embedding_provider=embedding,
        transport=transport,
        ops_logger=ops_logger,
    )


class TestClientPropagation:
    @pytest.mark.asyncio
    async def test_ingest_propagates_to_subscribed_agent(
        self,
        fact_repo: InMemoryFactRepository,
        agent_repo: InMemoryAgentRepository,
        sub_repo: InMemorySubscriptionRepository,
        event_log: InMemoryEventLog,
        transport: InProcessTransport,
        embedding: MockEmbeddingProvider,
        ops_logger: InMemoryOpsLogger,
    ) -> None:
        """Agent B subscribes to api.*, Agent A ingests with tag api.orders → B gets event."""
        received: list[PropagationEvent] = []

        client_a = _make_client(
            "agent-a",
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            sub_repo=sub_repo,
            event_log=event_log,
            transport=transport,
            embedding=embedding,
            ops_logger=ops_logger,
        )

        client_b = _make_client(
            "agent-b",
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            sub_repo=sub_repo,
            event_log=event_log,
            transport=transport,
            embedding=embedding,
            ops_logger=ops_logger,
            subscriptions=[SubscriptionConfig(topic="api.*", priority="high")],
        )

        async def on_fact(event: PropagationEvent) -> None:
            received.append(event)

        client_b.on_fact(on_fact)

        await client_a.connect()
        await client_b.connect()

        result = await client_a.ingest(
            FactContent(subject="api-orders", predicate="has_status", object="healthy"),
            SourceType.AGENT_EXTRACTION,
            tags=["api.orders"],
        )

        assert result.accepted
        assert result.fact is not None
        assert len(received) == 1
        assert received[0].fact_id == result.fact.id
        assert received[0].target_agent_id == "agent-b"

        await client_a.disconnect()
        await client_b.disconnect()

    @pytest.mark.asyncio
    async def test_source_agent_does_not_receive_own_fact(
        self,
        fact_repo: InMemoryFactRepository,
        agent_repo: InMemoryAgentRepository,
        sub_repo: InMemorySubscriptionRepository,
        event_log: InMemoryEventLog,
        transport: InProcessTransport,
        embedding: MockEmbeddingProvider,
        ops_logger: InMemoryOpsLogger,
    ) -> None:
        received: list[PropagationEvent] = []

        client_a = _make_client(
            "agent-a",
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            sub_repo=sub_repo,
            event_log=event_log,
            transport=transport,
            embedding=embedding,
            ops_logger=ops_logger,
            subscriptions=[SubscriptionConfig(topic="*")],
        )

        async def on_fact(event: PropagationEvent) -> None:
            received.append(event)

        client_a.on_fact(on_fact)
        await client_a.connect()

        await client_a.ingest(
            FactContent(subject="service-x", predicate="has_state", object="nominal"),
            SourceType.AGENT_EXTRACTION,
            tags=["anything"],
        )

        assert len(received) == 0
        await client_a.disconnect()

    @pytest.mark.asyncio
    async def test_replay_returns_undelivered_events(
        self,
        fact_repo: InMemoryFactRepository,
        agent_repo: InMemoryAgentRepository,
        sub_repo: InMemorySubscriptionRepository,
        event_log: InMemoryEventLog,
        transport: InProcessTransport,
        embedding: MockEmbeddingProvider,
        ops_logger: InMemoryOpsLogger,
    ) -> None:
        """Agent B is not connected when A ingests. On connect, B replays missed events."""
        # Agent A connects and ingests
        client_a = _make_client(
            "agent-a",
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            sub_repo=sub_repo,
            event_log=event_log,
            transport=transport,
            embedding=embedding,
            ops_logger=ops_logger,
        )

        # Manually set up agent-b's subscription in the repo before agent-a ingests
        # (simulating agent-b was previously connected)
        from uuid import uuid4

        from mycelium.domain.types import Priority, Subscription

        sub = Subscription(
            id=uuid4(),
            agent_id="agent-b",
            topic="api.*",
            priority=Priority.HIGH,
        )
        await sub_repo.sync_subscriptions("agent-b", [sub])

        await client_a.connect()

        result = await client_a.ingest(
            FactContent(subject="api-orders", predicate="has_status", object="healthy"),
            SourceType.AGENT_EXTRACTION,
            tags=["api.orders"],
        )
        assert result.accepted

        # Now agent-b connects and replays
        client_b = _make_client(
            "agent-b",
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            sub_repo=sub_repo,
            event_log=event_log,
            transport=transport,
            embedding=embedding,
            ops_logger=ops_logger,
            subscriptions=[SubscriptionConfig(topic="api.*", priority="high")],
        )

        received: list[PropagationEvent] = []

        async def on_fact(event: PropagationEvent) -> None:
            received.append(event)

        client_b.on_fact(on_fact)
        await client_b.connect()

        # on_fact callback was registered before connect, so connect() auto-replayed
        assert len(received) == 1
        assert result.fact is not None
        assert received[0].fact_id == result.fact.id

        await client_a.disconnect()
        await client_b.disconnect()

    @pytest.mark.asyncio
    async def test_replay_manual_with_deliver(
        self,
        fact_repo: InMemoryFactRepository,
        agent_repo: InMemoryAgentRepository,
        sub_repo: InMemorySubscriptionRepository,
        event_log: InMemoryEventLog,
        transport: InProcessTransport,
        embedding: MockEmbeddingProvider,
        ops_logger: InMemoryOpsLogger,
    ) -> None:
        """replay(deliver=True) delivers events to callback."""
        from uuid import uuid4

        from mycelium.domain.types import Priority, Subscription

        # Set up subscription
        sub = Subscription(
            id=uuid4(),
            agent_id="agent-b",
            topic="*",
            priority=Priority.NORMAL,
        )
        await sub_repo.sync_subscriptions("agent-b", [sub])

        # Agent A ingests
        client_a = _make_client(
            "agent-a",
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            sub_repo=sub_repo,
            event_log=event_log,
            transport=transport,
            embedding=embedding,
            ops_logger=ops_logger,
        )
        await client_a.connect()
        await client_a.ingest(
            FactContent(subject="service-x", predicate="has_state", object="nominal"),
            SourceType.AGENT_EXTRACTION,
            tags=["stuff"],
        )

        # Agent B connects without callback, then registers it and replays
        client_b = _make_client(
            "agent-b",
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            sub_repo=sub_repo,
            event_log=event_log,
            transport=transport,
            embedding=embedding,
            ops_logger=ops_logger,
            subscriptions=[SubscriptionConfig(topic="*")],
        )
        await client_b.connect()

        received: list[PropagationEvent] = []

        async def on_fact(event: PropagationEvent) -> None:
            received.append(event)

        client_b.on_fact(on_fact)

        # Without deliver, just returns events
        events = await client_b.replay()
        assert len(events) == 1
        assert len(received) == 0

        # With deliver, calls callback
        events = await client_b.replay(deliver=True)
        assert len(received) == 1

        await client_a.disconnect()
        await client_b.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_unsubscribes_transport(
        self,
        fact_repo: InMemoryFactRepository,
        agent_repo: InMemoryAgentRepository,
        sub_repo: InMemorySubscriptionRepository,
        event_log: InMemoryEventLog,
        transport: InProcessTransport,
        embedding: MockEmbeddingProvider,
        ops_logger: InMemoryOpsLogger,
    ) -> None:
        client = _make_client(
            "agent-a",
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            sub_repo=sub_repo,
            event_log=event_log,
            transport=transport,
            embedding=embedding,
            ops_logger=ops_logger,
        )

        await client.connect()
        assert transport.has_listener("agent-a")

        await client.disconnect()
        assert not transport.has_listener("agent-a")
