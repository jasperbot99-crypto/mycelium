"""Integration tests for PostgreSQL storage implementations.

These tests require a running PostgreSQL with the mycelium schema applied.
Run: pytest tests/integration/ -v
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
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

if TYPE_CHECKING:
    from mycelium.storage.postgres.repositories import (
        PostgresAgentRepository,
        PostgresConflictRepository,
        PostgresEventLog,
        PostgresFactRepository,
        PostgresRelationRepository,
        PostgresSubscriptionRepository,
    )


def _make_agent(agent_id: str = "test-agent", role: str = "tester") -> AgentRecord:
    return AgentRecord(id=agent_id, role=role, last_seen_at=datetime.now())


def _make_fact(
    agent_id: str = "test-agent",
    subject: str = "api-orders",
    predicate: str = "has_status",
    obj: str = "healthy",
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
        created_at=datetime.now(),
        tags=tags or [],
        embedding=embedding,
    )


# --- FactRepository ---


class TestPostgresFactRepository:
    @pytest.mark.asyncio
    async def test_insert_and_get(
        self, fact_repo: PostgresFactRepository, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact = _make_fact()
        await fact_repo.insert(fact)

        retrieved = await fact_repo.get_by_id(fact.id)
        assert retrieved is not None
        assert retrieved.content.subject == "api-orders"
        assert retrieved.source_type == SourceType.AGENT_EXTRACTION

    @pytest.mark.asyncio
    async def test_get_nonexistent(
        self, fact_repo: PostgresFactRepository, clean_db: None
    ) -> None:
        assert await fact_repo.get_by_id(uuid4()) is None

    @pytest.mark.asyncio
    async def test_find_by_subject(
        self, fact_repo: PostgresFactRepository, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        await fact_repo.insert(_make_fact(subject="api-orders"))
        await fact_repo.insert(_make_fact(subject="api-orders"))
        await fact_repo.insert(_make_fact(subject="api-auth"))

        results = await fact_repo.find_by_subject("api-orders")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_find_by_subject_case_insensitive(
        self, fact_repo: PostgresFactRepository, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        await fact_repo.insert(_make_fact(subject="API-Orders"))

        results = await fact_repo.find_by_subject("api-orders")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_expire_and_active_filter(
        self, fact_repo: PostgresFactRepository, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact = _make_fact(subject="api-orders")
        await fact_repo.insert(fact)
        await fact_repo.expire(fact.id)

        active = await fact_repo.find_by_subject("api-orders", active_only=True)
        all_facts = await fact_repo.find_by_subject("api-orders", active_only=False)
        assert len(active) == 0
        assert len(all_facts) == 1

    @pytest.mark.asyncio
    async def test_find_by_embedding(
        self, fact_repo: PostgresFactRepository, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        # Use 1536-dim vectors (text-embedding-3-small dimension, matches schema)
        vec1 = [1.0] + [0.0] * 1535
        vec2 = [0.0, 1.0] + [0.0] * 1534
        await fact_repo.insert(_make_fact(subject="a", embedding=vec1))
        await fact_repo.insert(_make_fact(subject="b", embedding=vec2))

        results = await fact_repo.find_by_embedding(vec1, min_similarity=0.9)
        assert len(results) == 1
        assert results[0][0].content.subject == "a"

    @pytest.mark.asyncio
    async def test_find_by_tags(
        self, fact_repo: PostgresFactRepository, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        await fact_repo.insert(_make_fact(tags=["api", "orders"]))
        await fact_repo.insert(_make_fact(tags=["infrastructure"]))

        results = await fact_repo.find_by_tags(["api"])
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_update_scores(
        self, fact_repo: PostgresFactRepository, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact = _make_fact()
        await fact_repo.insert(fact)

        await fact_repo.update_scores(fact.id, confidence=0.95, corroboration_count=3)
        retrieved = await fact_repo.get_by_id(fact.id)
        assert retrieved is not None
        assert abs(retrieved.confidence - 0.95) < 1e-9
        assert retrieved.corroboration_count == 3

    @pytest.mark.asyncio
    async def test_record_access(
        self, fact_repo: PostgresFactRepository, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact = _make_fact()
        await fact_repo.insert(fact)

        await fact_repo.record_access(fact.id)
        await fact_repo.record_access(fact.id)

        retrieved = await fact_repo.get_by_id(fact.id)
        assert retrieved is not None
        assert retrieved.access_count == 2
        assert retrieved.last_accessed_at is not None

    @pytest.mark.asyncio
    async def test_update_verification(
        self, fact_repo: PostgresFactRepository, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact = _make_fact()
        await fact_repo.insert(fact)

        await fact_repo.update_verification(fact.id, VerificationStatus.VERIFIED)
        retrieved = await fact_repo.get_by_id(fact.id)
        assert retrieved is not None
        assert retrieved.verification_status == VerificationStatus.VERIFIED
        assert retrieved.last_verified_at is not None

    @pytest.mark.asyncio
    async def test_update_conflict_status(
        self, fact_repo: PostgresFactRepository, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact = _make_fact()
        await fact_repo.insert(fact)

        await fact_repo.update_conflict_status(fact.id, "unresolved")
        retrieved = await fact_repo.get_by_id(fact.id)
        assert retrieved is not None
        assert retrieved.conflict_status == "unresolved"


# --- AgentRepository ---


class TestPostgresAgentRepository:
    @pytest.mark.asyncio
    async def test_upsert_and_get(
        self, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        agent = _make_agent()
        await agent_repo.upsert(agent)

        retrieved = await agent_repo.get_by_id("test-agent")
        assert retrieved is not None
        assert retrieved.role == "tester"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(
        self, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent(role="trader"))
        await agent_repo.upsert(_make_agent(role="researcher"))

        agent = await agent_repo.get_by_id("test-agent")
        assert agent is not None
        assert agent.role == "researcher"

    @pytest.mark.asyncio
    async def test_update_trust_stats(
        self, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        await agent_repo.update_trust_stats(
            "test-agent", facts_contributed_delta=10, facts_contradicted_delta=2,
        )

        agent = await agent_repo.get_by_id("test-agent")
        assert agent is not None
        assert agent.facts_contributed == 10
        assert agent.facts_contradicted == 2
        assert abs(agent.contradiction_rate - 0.2) < 1e-9

    @pytest.mark.asyncio
    async def test_list_all(
        self, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent("a1"))
        await agent_repo.upsert(_make_agent("a2"))
        agents = await agent_repo.list_all()
        assert len(agents) == 2

    @pytest.mark.asyncio
    async def test_update_trust_stats_applies_trust_delta(
        self, agent_repo: PostgresAgentRepository, clean_db: None
    ) -> None:
        await agent_repo.upsert(_make_agent())
        await agent_repo.update_trust_stats("test-agent", trust_score_delta=0.15)
        await agent_repo.update_trust_stats("test-agent", trust_score_delta=-0.9)

        agent = await agent_repo.get_by_id("test-agent")
        assert agent is not None
        assert agent.trust_score == 0.0


# --- ConflictRepository ---


class TestPostgresConflictRepository:
    @pytest.mark.asyncio
    async def test_insert_and_find(
        self,
        conflict_repo: PostgresConflictRepository,
        fact_repo: PostgresFactRepository,
        agent_repo: PostgresAgentRepository,
        clean_db: None,
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact_a = _make_fact()
        fact_b = _make_fact()
        await fact_repo.insert(fact_a)
        await fact_repo.insert(fact_b)

        conflict = Conflict(
            id=uuid4(),
            fact_a_id=fact_a.id,
            fact_b_id=fact_b.id,
            status=ConflictStatus.DETECTED,
        )
        await conflict_repo.insert(conflict)

        results = await conflict_repo.find_for_fact(fact_a.id)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_update_resolution(
        self,
        conflict_repo: PostgresConflictRepository,
        fact_repo: PostgresFactRepository,
        agent_repo: PostgresAgentRepository,
        clean_db: None,
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact_a = _make_fact()
        fact_b = _make_fact()
        await fact_repo.insert(fact_a)
        await fact_repo.insert(fact_b)

        conflict = Conflict(
            id=uuid4(),
            fact_a_id=fact_a.id,
            fact_b_id=fact_b.id,
            status=ConflictStatus.DETECTED,
        )
        await conflict_repo.insert(conflict)

        await conflict_repo.update_resolution(
            conflict.id, ConflictStatus.AUTO_RESOLVED, fact_a.id, "system"
        )

        updated = await conflict_repo.get_by_id(conflict.id)
        assert updated is not None
        assert updated.status == ConflictStatus.AUTO_RESOLVED
        assert updated.winning_fact_id == fact_a.id


# --- EventLog ---


class TestPostgresEventLog:
    @pytest.mark.asyncio
    async def test_append_and_get(
        self,
        event_log: PostgresEventLog,
        fact_repo: PostgresFactRepository,
        agent_repo: PostgresAgentRepository,
        clean_db: None,
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact = _make_fact()
        await fact_repo.insert(fact)

        event = PropagationEvent(
            id=uuid4(),
            fact_id=fact.id,
            target_agent_id="test-agent",
            reason="subscription match",
            priority=Priority.NORMAL,
        )
        stored = await event_log.append(event)
        assert stored.sequence_num > 0

    @pytest.mark.asyncio
    async def test_mark_delivered(
        self,
        event_log: PostgresEventLog,
        fact_repo: PostgresFactRepository,
        agent_repo: PostgresAgentRepository,
        clean_db: None,
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact = _make_fact()
        await fact_repo.insert(fact)

        event = PropagationEvent(
            id=uuid4(),
            fact_id=fact.id,
            target_agent_id="test-agent",
            reason="test",
            priority=Priority.NORMAL,
        )
        await event_log.append(event)
        await event_log.mark_delivered(event.id)

        undelivered = await event_log.get_undelivered("test-agent")
        assert len(undelivered) == 0


# --- RelationRepository ---


class TestPostgresRelationRepository:
    @pytest.mark.asyncio
    async def test_insert_and_find(
        self,
        relation_repo: PostgresRelationRepository,
        fact_repo: PostgresFactRepository,
        agent_repo: PostgresAgentRepository,
        clean_db: None,
    ) -> None:
        await agent_repo.upsert(_make_agent())
        fact_a = _make_fact()
        fact_b = _make_fact()
        await fact_repo.insert(fact_a)
        await fact_repo.insert(fact_b)

        relation = FactRelation(
            id=uuid4(),
            source_fact_id=fact_a.id,
            target_fact_id=fact_b.id,
            relation_type=RelationType.SUPERSEDES,
            created_by="test-agent",
        )
        await relation_repo.insert(relation)

        results = await relation_repo.find_for_fact(fact_a.id)
        assert len(results) == 1
        assert results[0].relation_type == RelationType.SUPERSEDES


# --- SubscriptionRepository ---


class TestPostgresSubscriptionRepository:
    @pytest.mark.asyncio
    async def test_sync_and_get(
        self,
        subscription_repo: PostgresSubscriptionRepository,
        agent_repo: PostgresAgentRepository,
        clean_db: None,
    ) -> None:
        await agent_repo.upsert(_make_agent())
        subs = [
            Subscription(
                id=uuid4(), agent_id="test-agent",
                topic="api.*", priority=Priority.HIGH,
            ),
            Subscription(
                id=uuid4(), agent_id="test-agent",
                topic="infra", priority=Priority.NORMAL,
            ),
        ]
        await subscription_repo.sync_subscriptions("test-agent", subs)

        retrieved = await subscription_repo.get_for_agent("test-agent")
        assert len(retrieved) == 2

    @pytest.mark.asyncio
    async def test_sync_replaces(
        self,
        subscription_repo: PostgresSubscriptionRepository,
        agent_repo: PostgresAgentRepository,
        clean_db: None,
    ) -> None:
        await agent_repo.upsert(_make_agent())
        await subscription_repo.sync_subscriptions("test-agent", [
            Subscription(id=uuid4(), agent_id="test-agent", topic="old", priority=Priority.LOW),
        ])
        await subscription_repo.sync_subscriptions("test-agent", [
            Subscription(id=uuid4(), agent_id="test-agent", topic="new", priority=Priority.HIGH),
        ])

        retrieved = await subscription_repo.get_for_agent("test-agent")
        assert len(retrieved) == 1
        assert retrieved[0].topic == "new"
