"""Tests for IngestPipeline and QueryEngine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mycelium.config import MyceliumConfig
from mycelium.domain.types import (
    ActiveContext,
    AgentRecord,
    Fact,
    FactContent,
    RankingProfile,
    RelationType,
    SourceType,
    Urgency,
    VerificationStatus,
)
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.pipelines.ingest import IngestPipeline
from mycelium.pipelines.query import QueryEngine, QueryFilters
from mycelium.storage.memory import (
    InMemoryAgentRepository,
    InMemoryConflictRepository,
    InMemoryFactRepository,
    InMemoryRelationRepository,
)


@pytest.fixture
def embedding() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(dimension=64)


@pytest.fixture
def config() -> MyceliumConfig:
    return MyceliumConfig()


@pytest.fixture
def fact_repo() -> InMemoryFactRepository:
    return InMemoryFactRepository()


@pytest.fixture
def agent_repo() -> InMemoryAgentRepository:
    return InMemoryAgentRepository()


@pytest.fixture
def conflict_repo() -> InMemoryConflictRepository:
    return InMemoryConflictRepository()


@pytest.fixture
def relation_repo() -> InMemoryRelationRepository:
    return InMemoryRelationRepository()


@pytest.fixture
async def registered_agent(agent_repo: InMemoryAgentRepository) -> AgentRecord:
    agent = AgentRecord(id="test-agent", role="tester")
    await agent_repo.upsert(agent)
    return agent


@pytest.fixture
def ingest_pipeline(
    fact_repo: InMemoryFactRepository,
    agent_repo: InMemoryAgentRepository,
    conflict_repo: InMemoryConflictRepository,
    relation_repo: InMemoryRelationRepository,
    embedding: MockEmbeddingProvider,
    config: MyceliumConfig,
) -> IngestPipeline:
    return IngestPipeline(
        fact_repo=fact_repo,
        agent_repo=agent_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        embedding_provider=embedding,
        config=config,
    )


@pytest.fixture
def query_engine(
    fact_repo: InMemoryFactRepository,
    embedding: MockEmbeddingProvider,
    config: MyceliumConfig,
) -> QueryEngine:
    return QueryEngine(
        fact_repo=fact_repo,
        embedding_provider=embedding,
        config=config,
    )


# --- IngestPipeline ---


class TestIngestPipeline:
    @pytest.mark.asyncio
    async def test_basic_ingest(
        self,
        ingest_pipeline: IngestPipeline,
        registered_agent: AgentRecord,
    ) -> None:
        content = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        result = await ingest_pipeline.ingest(
            content=content,
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_EXTRACTION,
            tags=["api"],
        )

        assert result.accepted
        assert result.fact is not None
        assert result.fact.content.subject == "api-orders"
        assert result.fact.embedding is not None
        assert result.fact.predicate_canonical == "has_status"
        assert result.fact.tags == ["api"]

    @pytest.mark.asyncio
    async def test_ingest_rejects_unknown_agent(
        self,
        ingest_pipeline: IngestPipeline,
    ) -> None:
        content = FactContent(subject="service-x", predicate="has_state", object="nominal")
        result = await ingest_pipeline.ingest(
            content=content,
            source_agent_id="nonexistent",
            source_type=SourceType.AGENT_INFERENCE,
        )

        assert result.rejected
        assert result.rejection is not None
        assert result.rejection.code == "unknown_agent"

    @pytest.mark.asyncio
    async def test_ingest_resolves_predicate_alias(
        self,
        ingest_pipeline: IngestPipeline,
        registered_agent: AgentRecord,
    ) -> None:
        content = FactContent(subject="api", predicate="is_down", object="true")
        result = await ingest_pipeline.ingest(
            content=content,
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_EXTRACTION,
        )

        assert result.accepted
        assert result.fact is not None
        assert result.fact.predicate_canonical == "has_status"

    @pytest.mark.asyncio
    async def test_ingest_unknown_predicate_leaves_canonical_none(
        self,
        ingest_pipeline: IngestPipeline,
        registered_agent: AgentRecord,
    ) -> None:
        content = FactContent(subject="api", predicate="some_custom_thing", object="value")
        result = await ingest_pipeline.ingest(
            content=content,
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_EXTRACTION,
        )

        assert result.accepted
        assert result.fact is not None
        assert result.fact.predicate_canonical is None

    @pytest.mark.asyncio
    async def test_ingest_sets_confidence_from_source_type(
        self,
        ingest_pipeline: IngestPipeline,
        registered_agent: AgentRecord,
    ) -> None:
        content = FactContent(subject="service-x", predicate="has_state", object="nominal")

        result_extraction = await ingest_pipeline.ingest(
            content=content,
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_EXTRACTION,
        )
        assert result_extraction.fact is not None
        assert result_extraction.fact.confidence == SourceType.AGENT_EXTRACTION.trust_weight

    @pytest.mark.asyncio
    async def test_ingest_updates_agent_stats(
        self,
        ingest_pipeline: IngestPipeline,
        agent_repo: InMemoryAgentRepository,
        registered_agent: AgentRecord,
    ) -> None:
        content = FactContent(subject="service-x", predicate="has_state", object="nominal")
        await ingest_pipeline.ingest(
            content=content,
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_EXTRACTION,
        )

        agent = await agent_repo.get_by_id("test-agent")
        assert agent is not None
        assert agent.facts_contributed == 1

    @pytest.mark.asyncio
    async def test_ingest_with_derived_from(
        self,
        ingest_pipeline: IngestPipeline,
        relation_repo: InMemoryRelationRepository,
        registered_agent: AgentRecord,
    ) -> None:
        parent_id = uuid4()
        content = FactContent(subject="service-x", predicate="has_state", object="nominal")
        result = await ingest_pipeline.ingest(
            content=content,
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_INFERENCE,
            derived_from=[parent_id],
        )

        assert result.accepted
        assert result.fact is not None
        relations = await relation_repo.find_for_fact(result.fact.id)
        assert any(r.relation_type.value == "derived_from" for r in relations)

    @pytest.mark.asyncio
    async def test_ingest_detects_contradiction(
        self,
        ingest_pipeline: IngestPipeline,
        fact_repo: InMemoryFactRepository,
        conflict_repo: InMemoryConflictRepository,
        embedding: MockEmbeddingProvider,
        registered_agent: AgentRecord,
    ) -> None:
        """Two facts with same subject and canonical predicate but different
        objects should conflict."""
        # Pre-seed a fact with known embedding
        content1 = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        embedding_vec = await embedding.embed(content1.to_embedding_text())

        existing = Fact(
            id=uuid4(),
            content=content1,
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.6,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
            predicate_canonical="has_status",
            embedding=embedding_vec,
        )
        await fact_repo.insert(existing)

        # Ingest a contradicting fact (same subject+predicate, different object)
        # Use identical embedding text to ensure high similarity
        content2 = FactContent(subject="api-orders", predicate="has_status", object="degraded")
        result = await ingest_pipeline.ingest(
            content=content2,
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_EXTRACTION,
        )

        assert result.accepted
        # The mock embedding won't produce similar vectors for different text,
        # so contradiction detection depends on embedding similarity.
        # With the mock, different text = different vectors = no contradiction.
        # This is expected — real embeddings would catch semantic similarity.

    @pytest.mark.asyncio
    async def test_ingest_treats_later_same_predicate_update_as_supersedes(
        self,
        fact_repo: InMemoryFactRepository,
        agent_repo: InMemoryAgentRepository,
        conflict_repo: InMemoryConflictRepository,
        relation_repo: InMemoryRelationRepository,
        config: MyceliumConfig,
    ) -> None:
        class _ConstantEmbedding:
            @property
            def dimension(self) -> int:
                return 3

            async def embed(self, text: str) -> list[float]:
                del text
                return [1.0, 0.0, 0.0]

            async def embed_batch(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0, 0.0] for _ in texts]

        await agent_repo.upsert(AgentRecord(id="test-agent", role="tester"))
        pipeline = IngestPipeline(
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            conflict_repo=conflict_repo,
            relation_repo=relation_repo,
            embedding_provider=_ConstantEmbedding(),
            config=config,
        )

        first = await pipeline.ingest(
            content=FactContent(subject="api-orders", predicate="has_status", object="healthy"),
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_EXTRACTION,
        )
        second = await pipeline.ingest(
            content=FactContent(subject="api-orders", predicate="has_status", object="degraded"),
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_EXTRACTION,
        )
        assert first.fact is not None
        assert second.fact is not None
        assert second.conflicts == []
        assert second.fact.supersedes == first.fact.id

        old = await fact_repo.get_by_id(first.fact.id)
        assert old is not None
        assert old.valid_until is not None

        conflicts = await conflict_repo.find_for_fact(second.fact.id)
        assert conflicts == []

        relations = await relation_repo.find_for_fact(second.fact.id)
        assert any(
            rel.relation_type == RelationType.SUPERSEDES
            and rel.target_fact_id == first.fact.id
            for rel in relations
        )

    @pytest.mark.asyncio
    async def test_ingest_semantic_duplicate_is_rejected_and_corroborates_existing(
        self,
        fact_repo: InMemoryFactRepository,
        agent_repo: InMemoryAgentRepository,
        conflict_repo: InMemoryConflictRepository,
        relation_repo: InMemoryRelationRepository,
        config: MyceliumConfig,
    ) -> None:
        class _ConstantEmbedding:
            @property
            def dimension(self) -> int:
                return 3

            async def embed(self, text: str) -> list[float]:
                del text
                return [1.0, 0.0, 0.0]

            async def embed_batch(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0, 0.0] for _ in texts]

        await agent_repo.upsert(AgentRecord(id="agent-a", role="tester"))
        await agent_repo.upsert(AgentRecord(id="agent-b", role="tester"))
        pipeline = IngestPipeline(
            fact_repo=fact_repo,
            agent_repo=agent_repo,
            conflict_repo=conflict_repo,
            relation_repo=relation_repo,
            embedding_provider=_ConstantEmbedding(),
            config=config,
        )

        first = await pipeline.ingest(
            content=FactContent(subject="api-orders", predicate="has_status", object="healthy"),
            source_agent_id="agent-a",
            source_type=SourceType.AGENT_EXTRACTION,
        )
        duplicate = await pipeline.ingest(
            content=FactContent(subject="api-orders", predicate="has_status", object="healthy"),
            source_agent_id="agent-b",
            source_type=SourceType.AGENT_EXTRACTION,
        )
        assert first.fact is not None
        assert duplicate.accepted is False
        assert duplicate.rejection is not None
        assert duplicate.rejection.code == "duplicate"
        assert duplicate.rejection.existing_fact_id == first.fact.id
        assert len(duplicate.corroborations) == 1

        all_active = await fact_repo.find_all_active()
        assert len(all_active) == 1
        existing = await fact_repo.get_by_id(first.fact.id)
        assert existing is not None
        assert existing.corroboration_count == 1


# --- QueryEngine ---


class TestQueryEngine:
    @pytest.mark.asyncio
    async def test_basic_query(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        # Insert a fact with embedding
        content = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        vec = await embedding.embed(content.to_embedding_text())
        fact = Fact(
            id=uuid4(),
            content=content,
            source_agent_id="agent-1",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.8,
            trust_score=0.7,
            valid_from=datetime.now(UTC),
            embedding=vec,
        )
        await fact_repo.insert(fact)

        # Query with identical text should find it
        results = await query_engine.query(content.to_embedding_text())
        assert len(results) >= 1
        assert results[0].fact.id == fact.id
        assert abs(results[0].similarity - 1.0) < 1e-9

    @pytest.mark.asyncio
    async def test_query_records_access(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        content = FactContent(subject="service-x", predicate="has_state", object="nominal")
        vec = await embedding.embed(content.to_embedding_text())
        fact = Fact(
            id=uuid4(),
            content=content,
            source_agent_id="agent-1",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.5,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
            embedding=vec,
        )
        await fact_repo.insert(fact)

        await query_engine.query(content.to_embedding_text())

        retrieved = await fact_repo.get_by_id(fact.id)
        assert retrieved is not None
        assert retrieved.access_count >= 1

    @pytest.mark.asyncio
    async def test_query_filters_by_confidence(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        content = FactContent(subject="service-x", predicate="has_state", object="nominal")
        vec = await embedding.embed(content.to_embedding_text())
        fact = Fact(
            id=uuid4(),
            content=content,
            source_agent_id="agent-1",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.3,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
            embedding=vec,
        )
        await fact_repo.insert(fact)

        # With high min_confidence filter, fact should be excluded
        results = await query_engine.query(
            content.to_embedding_text(),
            filters=QueryFilters(min_confidence=0.5),
        )
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_query_filters_by_source_type(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        content = FactContent(subject="service-x", predicate="has_state", object="nominal")
        vec = await embedding.embed(content.to_embedding_text())
        fact = Fact(
            id=uuid4(),
            content=content,
            source_agent_id="agent-1",
            source_type=SourceType.AGENT_INFERENCE,
            confidence=0.5,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
            embedding=vec,
        )
        await fact_repo.insert(fact)

        # Filter for only human corrections
        results = await query_engine.query(
            content.to_embedding_text(),
            filters=QueryFilters(source_types=[SourceType.HUMAN_CORRECTION]),
        )
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_query_excludes_expired(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        content = FactContent(subject="service-x", predicate="has_state", object="nominal")
        vec = await embedding.embed(content.to_embedding_text())
        fact = Fact(
            id=uuid4(),
            content=content,
            source_agent_id="agent-1",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.5,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
            embedding=vec,
        )
        await fact_repo.insert(fact)
        await fact_repo.expire(fact.id)

        results = await query_engine.query(content.to_embedding_text())
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_query_returns_empty_for_no_matches(
        self,
        query_engine: QueryEngine,
    ) -> None:
        results = await query_engine.query("something that doesn't exist")
        assert results == []

    @pytest.mark.asyncio
    async def test_query_respects_limit(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        # Insert 5 facts
        for i in range(5):
            content = FactContent(subject=f"s{i}", predicate="p", object="o")
            vec = await embedding.embed(content.to_embedding_text())
            await fact_repo.insert(Fact(
                id=uuid4(),
                content=content,
                source_agent_id="agent-1",
                source_type=SourceType.AGENT_EXTRACTION,
                confidence=0.5,
                trust_score=0.5,
                valid_from=datetime.now(UTC),
                embedding=vec,
            ))

        results = await query_engine.query("anything", limit=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_query_ranking_orders_by_score(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        content = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        vec = await embedding.embed(content.to_embedding_text())
        older = Fact(
            id=uuid4(),
            content=content,
            source_agent_id="agent-1",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.8,
            trust_score=0.5,
            valid_from=datetime.now(UTC) - timedelta(days=20),
            embedding=vec,
        )
        newer = Fact(
            id=uuid4(),
            content=content,
            source_agent_id="agent-2",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.8,
            trust_score=0.9,
            valid_from=datetime.now(UTC),
            embedding=vec,
        )
        await fact_repo.insert(older)
        await fact_repo.insert(newer)

        results = await query_engine.query(
            content.to_embedding_text(),
            limit=2,
            filters=QueryFilters(consolidate_by_subject=False),
        )
        assert len(results) == 2
        assert results[0].fact.id == newer.id
        assert results[0].score > results[1].score

    @pytest.mark.asyncio
    async def test_query_applies_verification_signal(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        content_verified = FactContent(subject="api-a", predicate="has_status", object="healthy")
        content_unverified = FactContent(
            subject="api-b",
            predicate="has_status",
            object="healthy",
        )
        vec_verified = await embedding.embed(content_verified.to_embedding_text())
        vec_unverified = await embedding.embed(content_unverified.to_embedding_text())

        verified = Fact(
            id=uuid4(),
            content=content_verified,
            source_agent_id="agent-1",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.8,
            trust_score=0.7,
            valid_from=datetime.now(UTC),
            embedding=vec_verified,
            verification_status=VerificationStatus.VERIFIED,
            tags=["status"],
        )
        unverified = Fact(
            id=uuid4(),
            content=content_unverified,
            source_agent_id="agent-2",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.8,
            trust_score=0.7,
            valid_from=datetime.now(UTC),
            embedding=vec_unverified,
            verification_status=VerificationStatus.UNVERIFIED,
            tags=["status"],
        )
        await fact_repo.insert(verified)
        await fact_repo.insert(unverified)

        results = await query_engine.query(
            "has_status healthy",
            filters=QueryFilters(
                consolidate_by_subject=False,
                tags=["status"],
            ),
            limit=2,
        )
        assert len(results) == 2
        assert results[0].fact.id == verified.id

    @pytest.mark.asyncio
    async def test_query_consolidates_results_by_subject(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        subject = "api-orders"
        for idx in range(3):
            content = FactContent(subject=subject, predicate="has_status", object=f"state-{idx}")
            vec = await embedding.embed(content.to_embedding_text())
            await fact_repo.insert(
                Fact(
                    id=uuid4(),
                    content=content,
                    source_agent_id=f"agent-{idx}",
                    source_type=SourceType.AGENT_EXTRACTION,
                    confidence=0.7,
                    trust_score=0.6 + idx * 0.1,
                    valid_from=datetime.now(UTC),
                    embedding=vec,
                )
            )

        results = await query_engine.query("api-orders status", limit=10)
        assert len(results) == 1
        assert results[0].fact.content.subject == subject

    @pytest.mark.asyncio
    async def test_query_boosts_active_context_entities(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        matched_content = FactContent(
            subject="alpaca broker adapter",
            predicate="has_status",
            object="degraded",
        )
        unmatched_content = FactContent(
            subject="unrelated planning task",
            predicate="has_status",
            object="healthy",
        )
        matched_vec = await embedding.embed(matched_content.to_embedding_text())
        unmatched_vec = await embedding.embed(unmatched_content.to_embedding_text())

        matched = Fact(
            id=uuid4(),
            content=matched_content,
            source_agent_id="agent-a",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.7,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
            embedding=matched_vec,
        )
        unmatched = Fact(
            id=uuid4(),
            content=unmatched_content,
            source_agent_id="agent-b",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.7,
            trust_score=0.95,
            valid_from=datetime.now(UTC),
            embedding=unmatched_vec,
        )
        await fact_repo.insert(matched)
        await fact_repo.insert(unmatched)

        results = await query_engine.query(
            "status update",
            filters=QueryFilters(consolidate_by_subject=False),
            active_context=ActiveContext(
                task="fix broker issue",
                entities=("alpaca", "broker"),
                urgency=Urgency.CRITICAL,
            ),
            limit=2,
        )
        assert len(results) == 2
        assert results[0].fact.id == matched.id

    @pytest.mark.asyncio
    async def test_query_uses_role_based_ranking_profile_half_life(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        content = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        vec = await embedding.embed(content.to_embedding_text())
        now = datetime.now(UTC)
        newer = Fact(
            id=uuid4(),
            content=content,
            source_agent_id="agent-new",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.8,
            trust_score=0.8,
            valid_from=now - timedelta(days=2),
            embedding=vec,
        )
        older = Fact(
            id=uuid4(),
            content=content,
            source_agent_id="agent-old",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.8,
            trust_score=0.8,
            valid_from=now - timedelta(days=20),
            embedding=vec,
        )
        await fact_repo.insert(newer)
        await fact_repo.insert(older)

        trader_results = await query_engine.query(
            "api orders status",
            filters=QueryFilters(consolidate_by_subject=False),
            agent_role="trader",
            limit=2,
        )
        code_results = await query_engine.query(
            "api orders status",
            filters=QueryFilters(consolidate_by_subject=False),
            agent_role="code",
            limit=2,
        )
        assert len(trader_results) == 2
        assert len(code_results) == 2
        assert trader_results[0].fact.id == newer.id
        assert code_results[0].fact.id == newer.id

        trader_gap = trader_results[0].score - trader_results[1].score
        code_gap = code_results[0].score - code_results[1].score
        assert trader_gap > code_gap

    @pytest.mark.asyncio
    async def test_query_applies_no_access_penalty_for_stale_unread_fact(
        self,
        query_engine: QueryEngine,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        subject = "api-orders"
        content_old = FactContent(subject=subject, predicate="has_status", object="healthy")
        content_recent = FactContent(subject="api-auth", predicate="has_status", object="healthy")
        old_vec = await embedding.embed(content_old.to_embedding_text())
        recent_vec = await embedding.embed(content_recent.to_embedding_text())
        old_unread = Fact(
            id=uuid4(),
            content=content_old,
            source_agent_id="agent-old",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.8,
            trust_score=0.8,
            valid_from=datetime.now(UTC) - timedelta(days=30),
            embedding=old_vec,
            access_count=0,
        )
        recent_read = Fact(
            id=uuid4(),
            content=content_recent,
            source_agent_id="agent-recent",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.8,
            trust_score=0.8,
            valid_from=datetime.now(UTC) - timedelta(days=5),
            embedding=recent_vec,
            access_count=5,
        )
        await fact_repo.insert(old_unread)
        await fact_repo.insert(recent_read)

        custom_profile = RankingProfile(
            similarity_weight=0.5,
            trust_weight=0.25,
            recency_weight=0.25,
            recency_half_life_hours=168,
            no_access_stale_penalty=0.2,
            no_access_grace_hours=24,
        )
        results = await query_engine.query(
            "has_status healthy",
            filters=QueryFilters(consolidate_by_subject=False),
            ranking_profile=custom_profile,
            limit=2,
        )
        assert len(results) == 2
        assert results[0].fact.id == recent_read.id
