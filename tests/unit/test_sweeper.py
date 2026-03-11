"""Tests for ContradictionSweeper — post-commit background sweep."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mycelium.domain.types import (
    Fact,
    FactContent,
    SourceType,
)
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.pipelines.sweeper import ContradictionSweeper
from mycelium.storage.memory import (
    InMemoryConflictRepository,
    InMemoryFactRepository,
    InMemoryRelationRepository,
)


@pytest.fixture
def fact_repo() -> InMemoryFactRepository:
    return InMemoryFactRepository()


@pytest.fixture
def conflict_repo() -> InMemoryConflictRepository:
    return InMemoryConflictRepository()


@pytest.fixture
def relation_repo() -> InMemoryRelationRepository:
    return InMemoryRelationRepository()


@pytest.fixture
def embedding() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(dimension=64)


@pytest.fixture
def sweeper(
    fact_repo: InMemoryFactRepository,
    conflict_repo: InMemoryConflictRepository,
    relation_repo: InMemoryRelationRepository,
) -> ContradictionSweeper:
    return ContradictionSweeper(
        fact_repo=fact_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        contradiction_threshold=0.75,
        corroboration_threshold=0.95,
        sweep_interval_s=1,
    )


async def _make_fact(
    fact_repo: InMemoryFactRepository,
    embedding_provider: MockEmbeddingProvider,
    subject: str,
    predicate: str,
    obj: str,
    predicate_canonical: str | None = None,
    embedding_override: list[float] | None = None,
) -> Fact:
    """Helper to create and store a fact with embedding."""
    content = FactContent(subject=subject, predicate=predicate, object=obj)
    emb = embedding_override or await embedding_provider.embed(content.to_embedding_text())
    fact = Fact(
        id=uuid4(),
        content=content,
        source_agent_id="test-agent",
        source_type=SourceType.AGENT_EXTRACTION,
        confidence=0.6,
        trust_score=0.5,
        valid_from=datetime.now(UTC),
        created_at=datetime.now(UTC),
        predicate_canonical=predicate_canonical,
        embedding=emb,
    )
    return await fact_repo.insert(fact)


class TestContradictionSweeper:
    @pytest.mark.asyncio
    async def test_sweep_no_facts(self, sweeper: ContradictionSweeper) -> None:
        result = await sweeper.sweep()
        assert result.facts_scanned == 0
        assert result.contradictions_found == 0
        assert result.corroborations_found == 0

    @pytest.mark.asyncio
    async def test_sweep_updates_last_sweep_at(
        self, sweeper: ContradictionSweeper
    ) -> None:
        assert sweeper.last_sweep_at is None
        await sweeper.sweep()
        assert sweeper.last_sweep_at is not None

    @pytest.mark.asyncio
    async def test_sweep_finds_contradiction_with_identical_embeddings(
        self,
        sweeper: ContradictionSweeper,
        fact_repo: InMemoryFactRepository,
        conflict_repo: InMemoryConflictRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        """Two facts with identical embeddings but different objects = contradiction."""
        shared_embedding = [0.1] * 64

        await _make_fact(
            fact_repo, embedding, "api-orders", "has_status", "healthy",
            predicate_canonical="has_status",
            embedding_override=shared_embedding,
        )
        await _make_fact(
            fact_repo, embedding, "api-orders", "has_status", "degraded",
            predicate_canonical="has_status",
            embedding_override=shared_embedding,
        )

        result = await sweeper.sweep()
        assert result.facts_scanned == 2
        assert result.contradictions_found >= 1

        # Conflict record should be created
        all_conflicts = await conflict_repo.find_by_status(
            __import__("mycelium.domain.types", fromlist=["ConflictStatus"]).ConflictStatus.DETECTED
        )
        assert len(all_conflicts) >= 1

    @pytest.mark.asyncio
    async def test_sweep_finds_corroboration_with_identical_embeddings(
        self,
        sweeper: ContradictionSweeper,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        """Two facts with identical embeddings and same object = corroboration."""
        shared_embedding = [0.1] * 64

        await _make_fact(
            fact_repo, embedding, "api-orders", "has_status", "healthy",
            predicate_canonical="has_status",
            embedding_override=shared_embedding,
        )
        await _make_fact(
            fact_repo, embedding, "api-orders", "has_status", "healthy",
            predicate_canonical="has_status",
            embedding_override=shared_embedding,
        )

        result = await sweeper.sweep()
        assert result.corroborations_found >= 1

    @pytest.mark.asyncio
    async def test_sweep_skips_already_recorded_conflicts(
        self,
        sweeper: ContradictionSweeper,
        fact_repo: InMemoryFactRepository,
        conflict_repo: InMemoryConflictRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        """Running sweep twice should not duplicate conflict records."""
        shared_embedding = [0.1] * 64

        await _make_fact(
            fact_repo, embedding, "api-orders", "has_status", "healthy",
            predicate_canonical="has_status",
            embedding_override=shared_embedding,
        )
        await _make_fact(
            fact_repo, embedding, "api-orders", "has_status", "degraded",
            predicate_canonical="has_status",
            embedding_override=shared_embedding,
        )

        # First sweep
        result1 = await sweeper.sweep()
        assert result1.contradictions_found >= 1

        # Second sweep — same facts, should not re-record
        result2 = await sweeper.sweep()
        assert result2.contradictions_found == 0

    @pytest.mark.asyncio
    async def test_sweep_ignores_unrelated_facts(
        self,
        sweeper: ContradictionSweeper,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        """Facts with different subjects should not trigger conflicts."""
        await _make_fact(fact_repo, embedding, "service-a", "has_status", "healthy")
        await _make_fact(fact_repo, embedding, "service-b", "has_status", "degraded")

        result = await sweeper.sweep()
        assert result.contradictions_found == 0
        assert result.corroborations_found == 0

    @pytest.mark.asyncio
    async def test_sweep_skips_facts_without_embedding(
        self,
        sweeper: ContradictionSweeper,
        fact_repo: InMemoryFactRepository,
    ) -> None:
        """Facts without embeddings should be skipped."""
        fact = Fact(
            id=uuid4(),
            content=FactContent(subject="x", predicate="y", object="z"),
            source_agent_id="test-agent",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.5,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
            embedding=None,
        )
        await fact_repo.insert(fact)

        result = await sweeper.sweep()
        assert result.facts_scanned == 1
        assert result.contradictions_found == 0

    @pytest.mark.asyncio
    async def test_start_stop(self, sweeper: ContradictionSweeper) -> None:
        assert not sweeper.running
        await sweeper.start()
        assert sweeper.running
        await sweeper.stop()
        assert not sweeper.running

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, sweeper: ContradictionSweeper) -> None:
        await sweeper.start()
        await sweeper.start()  # should not raise
        assert sweeper.running
        await sweeper.stop()

    @pytest.mark.asyncio
    async def test_sweep_sets_conflict_status_on_facts(
        self,
        sweeper: ContradictionSweeper,
        fact_repo: InMemoryFactRepository,
        embedding: MockEmbeddingProvider,
    ) -> None:
        """Contradicting facts should get conflict_status='unresolved'."""
        shared_embedding = [0.1] * 64

        f1 = await _make_fact(
            fact_repo, embedding, "api-orders", "has_status", "healthy",
            predicate_canonical="has_status",
            embedding_override=shared_embedding,
        )
        f2 = await _make_fact(
            fact_repo, embedding, "api-orders", "has_status", "degraded",
            predicate_canonical="has_status",
            embedding_override=shared_embedding,
        )

        await sweeper.sweep()

        f1_updated = await fact_repo.get_by_id(f1.id)
        f2_updated = await fact_repo.get_by_id(f2.id)
        assert f1_updated is not None
        assert f2_updated is not None
        assert f1_updated.conflict_status == "unresolved"
        assert f2_updated.conflict_status == "unresolved"
