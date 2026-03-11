"""Tests for MyceliumClient — end-to-end with in-memory backends."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mycelium.client.client import MyceliumClient
from mycelium.config import MyceliumConfig, SubscriptionConfig
from mycelium.domain.types import (
    ActiveContext,
    Conflict,
    ConflictStatus,
    FactContent,
    FeedbackSignal,
    RelationType,
    SourceType,
    Urgency,
    VerificationMethod,
    VerificationStatus,
)
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.storage.memory import (
    InMemoryAgentRepository,
    InMemoryConflictRepository,
    InMemoryEventLog,
    InMemoryFactRepository,
    InMemoryRelationRepository,
    InMemorySubscriptionRepository,
)

if TYPE_CHECKING:
    from uuid import UUID


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
def relation_repo() -> InMemoryRelationRepository:
    return InMemoryRelationRepository()


@pytest.fixture
def conflict_repo() -> InMemoryConflictRepository:
    return InMemoryConflictRepository()


@pytest.fixture
def config() -> MyceliumConfig:
    return MyceliumConfig(
        subscriptions=[
            SubscriptionConfig(topic="api.*", priority="high"),
        ],
    )


@pytest.fixture
def client(
    embedding: MockEmbeddingProvider,
    fact_repo: InMemoryFactRepository,
    agent_repo: InMemoryAgentRepository,
    relation_repo: InMemoryRelationRepository,
    conflict_repo: InMemoryConflictRepository,
    config: MyceliumConfig,
) -> MyceliumClient:
    return MyceliumClient(
        agent_id="test-agent",
        config=config,
        role="tester",
        fact_repo=fact_repo,
        agent_repo=agent_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        subscription_repo=InMemorySubscriptionRepository(),
        event_log=InMemoryEventLog(),
        embedding_provider=embedding,
    )


class TestMyceliumClient:
    @pytest.mark.asyncio
    async def test_connect_builds_conflict_llm_resolver_from_config(
        self,
        embedding: MockEmbeddingProvider,
        fact_repo: InMemoryFactRepository,
        agent_repo: InMemoryAgentRepository,
        relation_repo: InMemoryRelationRepository,
        conflict_repo: InMemoryConflictRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[tuple[str, str | None]] = []

        class _Resolver:
            async def resolve(self, conflict: object, fact_a: object, fact_b: object) -> object:
                del conflict, fact_a, fact_b
                raise RuntimeError("not used")

        def _fake_builder(provider: str, *, api_key: str | None, **_: object) -> _Resolver:
            calls.append((provider, api_key))
            return _Resolver()

        monkeypatch.setattr("mycelium.client.client.build_conflict_resolver", _fake_builder)

        client = MyceliumClient(
            agent_id="test-agent",
            config=MyceliumConfig(
                llm_provider="openai",
                llm_api_key="test-key",
                subscriptions=[SubscriptionConfig(topic="api.*", priority="high")],
            ),
            role="tester",
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            conflict_repo=conflict_repo,
            relation_repo=relation_repo,
            subscription_repo=InMemorySubscriptionRepository(),
            event_log=InMemoryEventLog(),
            embedding_provider=embedding,
        )

        await client.connect()
        assert calls == [("openai", "test-key")]
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_connect_creates_agent(
        self,
        client: MyceliumClient,
        agent_repo: InMemoryAgentRepository,
    ) -> None:
        await client.connect()

        agent = await agent_repo.get_by_id("test-agent")
        assert agent is not None
        assert agent.role == "tester"
        assert client.connected

    @pytest.mark.asyncio
    async def test_disconnect(self, client: MyceliumClient) -> None:
        await client.connect()
        await client.disconnect()
        assert not client.connected

    @pytest.mark.asyncio
    async def test_ingest_requires_connect(self, client: MyceliumClient) -> None:
        with pytest.raises(RuntimeError, match="not connected"):
            await client.ingest(
                FactContent(subject="x", predicate="y", object="z"),
                SourceType.AGENT_EXTRACTION,
            )

    @pytest.mark.asyncio
    async def test_ingest_and_query_roundtrip(
        self, client: MyceliumClient
    ) -> None:
        await client.connect()

        content = FactContent(
            subject="api-orders",
            predicate="has_status",
            object="healthy",
        )
        result = await client.ingest(
            content, SourceType.AGENT_EXTRACTION, tags=["api"]
        )
        assert result.accepted
        assert result.fact is not None

        # Query for it
        results = await client.query(content.to_embedding_text())
        assert len(results) >= 1
        assert results[0].fact.content.subject == "api-orders"

    @pytest.mark.asyncio
    async def test_correct_expires_old_fact(
        self, client: MyceliumClient, fact_repo: InMemoryFactRepository
    ) -> None:
        await client.connect()

        # Ingest original
        original = await client.ingest(
            FactContent(subject="api", predicate="version_is", object="1.0"),
            SourceType.AGENT_EXTRACTION,
        )
        assert original.fact is not None

        # Correct it
        correction = await client.correct(
            original.fact.id,
            FactContent(subject="api", predicate="version_is", object="2.0"),
            reason="version updated",
        )
        assert correction.accepted
        assert correction.fact is not None
        assert correction.fact.content.object == "2.0"

        # Old fact should be expired
        old = await fact_repo.get_by_id(original.fact.id)
        assert old is not None
        assert not old.is_active

    @pytest.mark.asyncio
    async def test_update_context(
        self, client: MyceliumClient, agent_repo: InMemoryAgentRepository
    ) -> None:
        await client.connect()

        ctx = ActiveContext(
            task="monitoring api health",
            entities=("api-orders", "api-auth"),
            urgency=Urgency.ELEVATED,
        )
        await client.update_context(ctx)

        agent = await agent_repo.get_by_id("test-agent")
        assert agent is not None
        assert agent.active_context.task == "monitoring api health"
        assert agent.active_context.urgency == Urgency.ELEVATED

    @pytest.mark.asyncio
    async def test_multiple_ingests_and_query(
        self, client: MyceliumClient
    ) -> None:
        await client.connect()

        facts_data = [
            ("api-orders", "has_status", "healthy"),
            ("api-auth", "has_status", "degraded"),
            ("postgres-main", "version_is", "16.2"),
        ]

        for subject, pred, obj in facts_data:
            await client.ingest(
                FactContent(subject=subject, predicate=pred, object=obj),
                SourceType.AGENT_EXTRACTION,
            )

        # Query should return results
        results = await client.query("api-orders has_status healthy")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_replay_returns_empty_without_events(
        self, client: MyceliumClient
    ) -> None:
        await client.connect()
        events = await client.replay()
        assert events == []

    @pytest.mark.asyncio
    async def test_verify_updates_fact_and_agent_trust(
        self,
        client: MyceliumClient,
        fact_repo: InMemoryFactRepository,
        agent_repo: InMemoryAgentRepository,
    ) -> None:
        await client.connect()
        ingested = await client.ingest(
            FactContent(subject="api-orders", predicate="has_status", object="healthy"),
            SourceType.AGENT_EXTRACTION,
            tags=["api.orders"],
        )
        assert ingested.fact is not None
        baseline_confidence = ingested.fact.confidence

        result = await client.verify(
            ingested.fact.id,
            method=VerificationMethod.SYSTEM_PROBE,
            status=VerificationStatus.VERIFIED,
            reason="live probe returned 200",
        )
        assert result.status == VerificationStatus.VERIFIED

        updated_fact = await fact_repo.get_by_id(ingested.fact.id)
        assert updated_fact is not None
        assert updated_fact.verification_status == VerificationStatus.VERIFIED
        assert updated_fact.last_verified_at is not None
        assert updated_fact.confidence > baseline_confidence

        source_agent = await agent_repo.get_by_id("test-agent")
        assert source_agent is not None
        assert source_agent.trust_score > 0.5

    @pytest.mark.asyncio
    async def test_corroborate_creates_relation_and_updates_scores(
        self,
        client: MyceliumClient,
        fact_repo: InMemoryFactRepository,
        relation_repo: InMemoryRelationRepository,
    ) -> None:
        await client.connect()

        original = await client.ingest(
            FactContent(subject="api-orders", predicate="has_status", object="healthy"),
            SourceType.AGENT_EXTRACTION,
            tags=["api.orders"],
        )
        corroborating = await client.ingest(
            FactContent(subject="api-orders", predicate="reports_health", object="healthy"),
            SourceType.AGENT_INFERENCE,
            tags=["api.orders"],
        )
        assert original.fact is not None
        assert corroborating.fact is not None

        result = await client.corroborate(
            original.fact.id,
            corroborating.fact.id,
            reason="independent agent confirmed",
        )
        assert result.corroboration_count >= 1

        updated = await fact_repo.get_by_id(original.fact.id)
        assert updated is not None
        assert updated.corroboration_count == result.corroboration_count
        assert updated.verification_status == VerificationStatus.VERIFIED

        relations = await relation_repo.find_for_fact(original.fact.id)
        corroboration_relations = [
            rel for rel in relations if rel.relation_type == RelationType.CORROBORATES
        ]
        assert len(corroboration_relations) >= 1

    @pytest.mark.asyncio
    async def test_verify_failed_reduces_agent_trust(
        self,
        client: MyceliumClient,
        agent_repo: InMemoryAgentRepository,
    ) -> None:
        await client.connect()
        ingested = await client.ingest(
            FactContent(subject="db-main", predicate="has_status", object="healthy"),
            SourceType.AGENT_EXTRACTION,
            tags=["database.main"],
        )
        assert ingested.fact is not None

        before = await agent_repo.get_by_id("test-agent")
        assert before is not None
        before_score = before.trust_score

        await client.verify(
            ingested.fact.id,
            method=VerificationMethod.SOURCE_RECHECK,
            status=VerificationStatus.FAILED,
            reason="source now reports degraded",
        )

        after = await agent_repo.get_by_id("test-agent")
        assert after is not None
        assert after.trust_score < before_score

    @pytest.mark.asyncio
    async def test_feedback_wrong_marks_failed_and_penalizes_trust(
        self,
        client: MyceliumClient,
        fact_repo: InMemoryFactRepository,
        agent_repo: InMemoryAgentRepository,
    ) -> None:
        await client.connect()
        ingested = await client.ingest(
            FactContent(subject="api-orders", predicate="has_status", object="healthy"),
            SourceType.AGENT_EXTRACTION,
        )
        assert ingested.fact is not None

        result = await client.feedback(
            ingested.fact.id,
            signal=FeedbackSignal.WRONG,
            reason="manual correction from operator",
        )
        assert result.signal == FeedbackSignal.WRONG
        assert result.verification_status == VerificationStatus.FAILED
        assert result.confidence_delta < 0
        assert result.trust_delta < 0

        updated_fact = await fact_repo.get_by_id(ingested.fact.id)
        assert updated_fact is not None
        assert updated_fact.verification_status == VerificationStatus.FAILED

        source_agent = await agent_repo.get_by_id("test-agent")
        assert source_agent is not None
        assert source_agent.facts_contradicted >= 1

    @pytest.mark.asyncio
    async def test_trust_evolves_over_many_interactions(
        self,
        client: MyceliumClient,
        agent_repo: InMemoryAgentRepository,
    ) -> None:
        await client.connect()
        baseline = await agent_repo.get_by_id("test-agent")
        assert baseline is not None
        baseline_score = baseline.trust_score

        fact_ids: list[UUID] = []
        for idx in range(6):
            ingested = await client.ingest(
                FactContent(
                    subject=f"service-{idx}",
                    predicate="has_status",
                    object="healthy",
                ),
                SourceType.AGENT_EXTRACTION,
            )
            assert ingested.fact is not None
            fact_ids.append(ingested.fact.id)

        for idx, fact_id in enumerate(fact_ids):
            status = VerificationStatus.VERIFIED if idx < 5 else VerificationStatus.FAILED
            await client.verify(
                fact_id,
                method=VerificationMethod.SYSTEM_PROBE,
                status=status,
                reason="long-run trust evolution check",
            )

        after = await agent_repo.get_by_id("test-agent")
        assert after is not None
        assert after.trust_score != baseline_score
        assert after.trust_score > baseline_score

    @pytest.mark.asyncio
    async def test_resolve_conflicts_auto_resolves_detected_conflict(
        self,
        client: MyceliumClient,
        fact_repo: InMemoryFactRepository,
        conflict_repo: InMemoryConflictRepository,
    ) -> None:
        await client.connect()

        low_authority = await client.ingest(
            FactContent(subject="api-orders", predicate="has_status", object="healthy"),
            SourceType.AGENT_INFERENCE,
            tags=["api.orders"],
        )
        high_authority = await client.ingest(
            FactContent(subject="api-orders", predicate="has_status", object="degraded"),
            SourceType.HUMAN_CORRECTION,
            tags=["api.orders"],
        )
        assert low_authority.fact is not None
        assert high_authority.fact is not None

        conflict = Conflict(
            id=high_authority.fact.id,
            fact_a_id=low_authority.fact.id,
            fact_b_id=high_authority.fact.id,
            status=ConflictStatus.DETECTED,
        )
        await conflict_repo.insert(conflict)

        results = await client.resolve_conflicts()
        assert any(result.status == ConflictStatus.AUTO_RESOLVED for result in results)

        losing = await fact_repo.get_by_id(low_authority.fact.id)
        assert losing is not None
        assert losing.valid_until is not None

    @pytest.mark.asyncio
    async def test_trace_provenance_returns_ancestor_chain(
        self,
        client: MyceliumClient,
    ) -> None:
        await client.connect()

        root = await client.ingest(
            FactContent(subject="api-orders", predicate="version_is", object="v1"),
            SourceType.AGENT_EXTRACTION,
            tags=["api.orders"],
        )
        assert root.fact is not None

        child = await client.ingest(
            FactContent(subject="api-orders", predicate="version_is", object="v2"),
            SourceType.HUMAN_CORRECTION,
            tags=["api.orders"],
            derived_from=[root.fact.id],
        )
        assert child.fact is not None

        chain = await client.trace_provenance(child.fact.id)
        ids = [entry.fact.id for entry in chain]
        assert child.fact.id in ids
        assert root.fact.id in ids
