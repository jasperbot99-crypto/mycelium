"""Tests for in-memory storage implementations."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from mycelium.domain.types import (
    AgentRecord,
    Conflict,
    ConflictStatus,
    Fact,
    FactContent,
    FactRelation,
    Priority,
    PropagationEvent,
    RelationType,
    SourceType,
    Subscription,
    VerificationStatus,
)
from mycelium.storage.memory import (
    InMemoryAgentRepository,
    InMemoryConflictRepository,
    InMemoryEventLog,
    InMemoryFactRepository,
    InMemoryRelationRepository,
    InMemorySubscriptionRepository,
)


def _make_fact(
    subject: str = "test-api",
    predicate: str = "has_status",
    obj: str = "healthy",
    agent_id: str = "agent-1",
    embedding: list[float] | None = None,
    tags: list[str] | None = None,
) -> Fact:
    return Fact(
        id=uuid4(),
        content=FactContent(subject=subject, predicate=predicate, object=obj),
        source_agent_id=agent_id,
        source_type=SourceType.AGENT_EXTRACTION,
        confidence=0.6,
        trust_score=0.5,
        valid_from=datetime.now(),
        tags=tags or [],
        embedding=embedding,
    )


# --- FactRepository ---


class TestInMemoryFactRepository:
    @pytest.fixture
    def repo(self) -> InMemoryFactRepository:
        return InMemoryFactRepository()

    @pytest.mark.asyncio
    async def test_insert_and_get(self, repo: InMemoryFactRepository) -> None:
        fact = _make_fact()
        stored = await repo.insert(fact)
        assert stored.id == fact.id

        retrieved = await repo.get_by_id(fact.id)
        assert retrieved is not None
        assert retrieved.content.subject == "test-api"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, repo: InMemoryFactRepository) -> None:
        assert await repo.get_by_id(uuid4()) is None

    @pytest.mark.asyncio
    async def test_find_by_subject(self, repo: InMemoryFactRepository) -> None:
        await repo.insert(_make_fact(subject="api-orders"))
        await repo.insert(_make_fact(subject="api-orders"))
        await repo.insert(_make_fact(subject="api-auth"))

        results = await repo.find_by_subject("api-orders")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_find_by_subject_case_insensitive(self, repo: InMemoryFactRepository) -> None:
        await repo.insert(_make_fact(subject="API-Orders"))
        results = await repo.find_by_subject("api-orders")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_find_by_subject_active_only(self, repo: InMemoryFactRepository) -> None:
        fact = _make_fact(subject="api-orders")
        await repo.insert(fact)
        await repo.expire(fact.id)

        assert len(await repo.find_by_subject("api-orders", active_only=True)) == 0
        assert len(await repo.find_by_subject("api-orders", active_only=False)) == 1

    @pytest.mark.asyncio
    async def test_find_by_embedding(self, repo: InMemoryFactRepository) -> None:
        vec = [1.0, 0.0, 0.0]
        await repo.insert(_make_fact(embedding=vec))
        await repo.insert(_make_fact(embedding=[0.0, 1.0, 0.0]))

        results = await repo.find_by_embedding(vec, min_similarity=0.9)
        assert len(results) == 1
        assert abs(results[0][1] - 1.0) < 1e-9

    @pytest.mark.asyncio
    async def test_find_by_tags(self, repo: InMemoryFactRepository) -> None:
        await repo.insert(_make_fact(tags=["api", "orders"]))
        await repo.insert(_make_fact(tags=["infrastructure"]))

        results = await repo.find_by_tags(["api"])
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_expire(self, repo: InMemoryFactRepository) -> None:
        fact = _make_fact()
        await repo.insert(fact)
        assert fact.is_active

        await repo.expire(fact.id)
        retrieved = await repo.get_by_id(fact.id)
        assert retrieved is not None
        assert not retrieved.is_active

    @pytest.mark.asyncio
    async def test_update_scores(self, repo: InMemoryFactRepository) -> None:
        fact = _make_fact()
        await repo.insert(fact)

        await repo.update_scores(fact.id, confidence=0.9, corroboration_count=3)
        retrieved = await repo.get_by_id(fact.id)
        assert retrieved is not None
        assert retrieved.confidence == 0.9
        assert retrieved.corroboration_count == 3

    @pytest.mark.asyncio
    async def test_record_access(self, repo: InMemoryFactRepository) -> None:
        fact = _make_fact()
        await repo.insert(fact)

        await repo.record_access(fact.id)
        await repo.record_access(fact.id)

        retrieved = await repo.get_by_id(fact.id)
        assert retrieved is not None
        assert retrieved.access_count == 2
        assert retrieved.last_accessed_at is not None

    @pytest.mark.asyncio
    async def test_update_verification(self, repo: InMemoryFactRepository) -> None:
        fact = _make_fact()
        await repo.insert(fact)

        await repo.update_verification(fact.id, VerificationStatus.VERIFIED)
        retrieved = await repo.get_by_id(fact.id)
        assert retrieved is not None
        assert retrieved.verification_status == VerificationStatus.VERIFIED
        assert retrieved.last_verified_at is not None

    @pytest.mark.asyncio
    async def test_find_created_since(self, repo: InMemoryFactRepository) -> None:
        old_fact = _make_fact()
        old_fact.created_at = datetime.now() - timedelta(hours=2)
        await repo.insert(old_fact)

        new_fact = _make_fact()
        await repo.insert(new_fact)

        since = datetime.now() - timedelta(hours=1)
        results = await repo.find_created_since(since)
        assert len(results) == 1
        assert results[0].id == new_fact.id

    @pytest.mark.asyncio
    async def test_update_conflict_status(self, repo: InMemoryFactRepository) -> None:
        fact = _make_fact()
        await repo.insert(fact)

        await repo.update_conflict_status(fact.id, "unresolved")
        retrieved = await repo.get_by_id(fact.id)
        assert retrieved is not None
        assert retrieved.is_conflicted


# --- AgentRepository ---


class TestInMemoryAgentRepository:
    @pytest.fixture
    def repo(self) -> InMemoryAgentRepository:
        return InMemoryAgentRepository()

    @pytest.mark.asyncio
    async def test_upsert_and_get(self, repo: InMemoryAgentRepository) -> None:
        agent = AgentRecord(id="agent-1", role="trader")
        await repo.upsert(agent)

        retrieved = await repo.get_by_id("agent-1")
        assert retrieved is not None
        assert retrieved.role == "trader"

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, repo: InMemoryAgentRepository) -> None:
        await repo.upsert(AgentRecord(id="agent-1", role="trader"))
        await repo.upsert(AgentRecord(id="agent-1", role="researcher"))

        retrieved = await repo.get_by_id("agent-1")
        assert retrieved is not None
        assert retrieved.role == "researcher"

    @pytest.mark.asyncio
    async def test_update_trust_stats(self, repo: InMemoryAgentRepository) -> None:
        await repo.upsert(AgentRecord(id="agent-1", role="trader"))
        await repo.update_trust_stats("agent-1", facts_contributed_delta=5, facts_contradicted_delta=1)

        agent = await repo.get_by_id("agent-1")
        assert agent is not None
        assert agent.facts_contributed == 5
        assert agent.facts_contradicted == 1
        assert abs(agent.contradiction_rate - 0.2) < 1e-9

    @pytest.mark.asyncio
    async def test_update_trust_stats_applies_trust_delta(
        self, repo: InMemoryAgentRepository
    ) -> None:
        await repo.upsert(AgentRecord(id="agent-1", role="trader", trust_score=0.5))
        await repo.update_trust_stats("agent-1", trust_score_delta=0.2)
        await repo.update_trust_stats("agent-1", trust_score_delta=-0.9)

        agent = await repo.get_by_id("agent-1")
        assert agent is not None
        assert agent.trust_score == 0.0

    @pytest.mark.asyncio
    async def test_list_all(self, repo: InMemoryAgentRepository) -> None:
        await repo.upsert(AgentRecord(id="a1", role="trader"))
        await repo.upsert(AgentRecord(id="a2", role="code"))
        assert len(await repo.list_all()) == 2


# --- ConflictRepository ---


class TestInMemoryConflictRepository:
    @pytest.fixture
    def repo(self) -> InMemoryConflictRepository:
        return InMemoryConflictRepository()

    @pytest.mark.asyncio
    async def test_insert_and_find(self, repo: InMemoryConflictRepository) -> None:
        fact_a_id = uuid4()
        fact_b_id = uuid4()
        conflict = Conflict(
            id=uuid4(),
            fact_a_id=fact_a_id,
            fact_b_id=fact_b_id,
            status=ConflictStatus.DETECTED,
        )
        await repo.insert(conflict)

        results = await repo.find_for_fact(fact_a_id)
        assert len(results) == 1
        assert results[0].fact_b_id == fact_b_id

    @pytest.mark.asyncio
    async def test_find_by_status(self, repo: InMemoryConflictRepository) -> None:
        await repo.insert(Conflict(id=uuid4(), fact_a_id=uuid4(), fact_b_id=uuid4(), status=ConflictStatus.DETECTED))
        await repo.insert(Conflict(id=uuid4(), fact_a_id=uuid4(), fact_b_id=uuid4(), status=ConflictStatus.AUTO_RESOLVED))

        detected = await repo.find_by_status(ConflictStatus.DETECTED)
        assert len(detected) == 1

    @pytest.mark.asyncio
    async def test_update_resolution(self, repo: InMemoryConflictRepository) -> None:
        conflict = Conflict(id=uuid4(), fact_a_id=uuid4(), fact_b_id=uuid4(), status=ConflictStatus.DETECTED)
        await repo.insert(conflict)

        winner = uuid4()
        await repo.update_resolution(conflict.id, ConflictStatus.AUTO_RESOLVED, winner, "system")

        updated = await repo.get_by_id(conflict.id)
        assert updated is not None
        assert updated.status == ConflictStatus.AUTO_RESOLVED
        assert updated.winning_fact_id == winner
        assert updated.resolved_at is not None


# --- EventLog ---


class TestInMemoryEventLog:
    @pytest.fixture
    def log(self) -> InMemoryEventLog:
        return InMemoryEventLog()

    @pytest.mark.asyncio
    async def test_append_assigns_sequence(self, log: InMemoryEventLog) -> None:
        e1 = PropagationEvent(id=uuid4(), fact_id=uuid4(), target_agent_id="a1", reason="test", priority=Priority.NORMAL)
        e2 = PropagationEvent(id=uuid4(), fact_id=uuid4(), target_agent_id="a1", reason="test", priority=Priority.NORMAL)

        stored1 = await log.append(e1)
        stored2 = await log.append(e2)

        assert stored1.sequence_num == 1
        assert stored2.sequence_num == 2

    @pytest.mark.asyncio
    async def test_get_since(self, log: InMemoryEventLog) -> None:
        for _ in range(5):
            await log.append(PropagationEvent(id=uuid4(), fact_id=uuid4(), target_agent_id="a1", reason="test", priority=Priority.NORMAL))

        results = await log.get_since("a1", since_sequence=3)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_mark_delivered(self, log: InMemoryEventLog) -> None:
        event = PropagationEvent(id=uuid4(), fact_id=uuid4(), target_agent_id="a1", reason="test", priority=Priority.NORMAL)
        await log.append(event)

        await log.mark_delivered(event.id)
        undelivered = await log.get_undelivered("a1")
        assert len(undelivered) == 0

    @pytest.mark.asyncio
    async def test_get_undelivered(self, log: InMemoryEventLog) -> None:
        await log.append(PropagationEvent(id=uuid4(), fact_id=uuid4(), target_agent_id="a1", reason="test", priority=Priority.NORMAL))
        await log.append(PropagationEvent(id=uuid4(), fact_id=uuid4(), target_agent_id="a2", reason="test", priority=Priority.NORMAL))

        undelivered = await log.get_undelivered("a1")
        assert len(undelivered) == 1


# --- RelationRepository ---


class TestInMemoryRelationRepository:
    @pytest.fixture
    def repo(self) -> InMemoryRelationRepository:
        return InMemoryRelationRepository()

    @pytest.mark.asyncio
    async def test_insert_and_find(self, repo: InMemoryRelationRepository) -> None:
        source_id = uuid4()
        rel = FactRelation(
            id=uuid4(),
            source_fact_id=source_id,
            target_fact_id=uuid4(),
            relation_type=RelationType.SUPERSEDES,
        )
        await repo.insert(rel)

        results = await repo.find_for_fact(source_id)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_filter_by_type(self, repo: InMemoryRelationRepository) -> None:
        fact_id = uuid4()
        await repo.insert(FactRelation(id=uuid4(), source_fact_id=fact_id, target_fact_id=uuid4(), relation_type=RelationType.SUPERSEDES))
        await repo.insert(FactRelation(id=uuid4(), source_fact_id=fact_id, target_fact_id=uuid4(), relation_type=RelationType.CORROBORATES))

        supersedes = await repo.find_for_fact(fact_id, relation_type=RelationType.SUPERSEDES)
        assert len(supersedes) == 1


# --- SubscriptionRepository ---


class TestInMemorySubscriptionRepository:
    @pytest.fixture
    def repo(self) -> InMemorySubscriptionRepository:
        return InMemorySubscriptionRepository()

    @pytest.mark.asyncio
    async def test_sync_and_get(self, repo: InMemorySubscriptionRepository) -> None:
        subs = [
            Subscription(id=uuid4(), agent_id="a1", topic="api.*", priority=Priority.HIGH),
            Subscription(id=uuid4(), agent_id="a1", topic="infra", priority=Priority.NORMAL),
        ]
        await repo.sync_subscriptions("a1", subs)

        retrieved = await repo.get_for_agent("a1")
        assert len(retrieved) == 2

    @pytest.mark.asyncio
    async def test_sync_replaces(self, repo: InMemorySubscriptionRepository) -> None:
        await repo.sync_subscriptions("a1", [
            Subscription(id=uuid4(), agent_id="a1", topic="old", priority=Priority.LOW),
        ])
        await repo.sync_subscriptions("a1", [
            Subscription(id=uuid4(), agent_id="a1", topic="new", priority=Priority.HIGH),
        ])

        retrieved = await repo.get_for_agent("a1")
        assert len(retrieved) == 1
        assert retrieved[0].topic == "new"

    @pytest.mark.asyncio
    async def test_get_all(self, repo: InMemorySubscriptionRepository) -> None:
        await repo.sync_subscriptions("a1", [Subscription(id=uuid4(), agent_id="a1", topic="t1", priority=Priority.NORMAL)])
        await repo.sync_subscriptions("a2", [Subscription(id=uuid4(), agent_id="a2", topic="t2", priority=Priority.NORMAL)])

        all_subs = await repo.get_all()
        assert len(all_subs) == 2
