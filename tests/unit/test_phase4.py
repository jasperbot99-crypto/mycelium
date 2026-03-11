"""Unit tests for Phase 4 conflict resolution and provenance workflows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from mycelium.config import MyceliumConfig
from mycelium.domain.types import (
    AgentRecord,
    Conflict,
    ConflictStatus,
    Fact,
    FactContent,
    RelationType,
    SourceType,
)
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.pipelines.conflict_resolution import (
    ConflictResolutionPipeline,
    LLMResolutionDecision,
)
from mycelium.pipelines.ingest import IngestPipeline
from mycelium.pipelines.provenance import ProvenancePipeline
from mycelium.storage.memory import (
    InMemoryAgentRepository,
    InMemoryConflictRepository,
    InMemoryFactRepository,
    InMemoryRelationRepository,
)


class _FixedLLMResolver:
    def __init__(
        self,
        winning_fact_id: UUID | None,
        confidence: float,
        *,
        escalate: bool = False,
    ) -> None:
        self._winning_fact_id = winning_fact_id
        self._confidence = confidence
        self._escalate = escalate

    async def resolve(
        self,
        conflict: Conflict,
        fact_a: Fact,
        fact_b: Fact,
    ) -> LLMResolutionDecision:
        del conflict, fact_a, fact_b
        return LLMResolutionDecision(
            winning_fact_id=self._winning_fact_id,
            confidence=self._confidence,
            rationale="test-decision",
            escalate=self._escalate,
        )


def _make_fact(
    *,
    source_type: SourceType,
    valid_from: datetime,
    metadata: dict[str, object] | None = None,
) -> Fact:
    return Fact(
        id=uuid4(),
        content=FactContent(subject="api-orders", predicate="has_status", object="value"),
        source_agent_id="agent-a",
        source_type=source_type,
        confidence=0.6,
        trust_score=0.5,
        valid_from=valid_from,
        metadata=metadata or {},
    )


@pytest.mark.asyncio
async def test_conflict_resolution_prefers_higher_source_authority() -> None:
    fact_repo = InMemoryFactRepository()
    conflict_repo = InMemoryConflictRepository()
    relation_repo = InMemoryRelationRepository()

    older = _make_fact(source_type=SourceType.AGENT_INFERENCE, valid_from=datetime.now(UTC))
    newer = _make_fact(source_type=SourceType.HUMAN_CORRECTION, valid_from=datetime.now(UTC))
    await fact_repo.insert(older)
    await fact_repo.insert(newer)

    conflict = Conflict(
        id=uuid4(),
        fact_a_id=older.id,
        fact_b_id=newer.id,
        status=ConflictStatus.DETECTED,
    )
    await conflict_repo.insert(conflict)

    pipeline = ConflictResolutionPipeline(
        fact_repo=fact_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        config=MyceliumConfig(),
    )

    results = await pipeline.resolve_pending()
    assert len(results) == 1
    assert results[0].status == ConflictStatus.AUTO_RESOLVED
    assert results[0].winning_fact_id == newer.id

    updated = await conflict_repo.get_by_id(conflict.id)
    assert updated is not None
    assert updated.status == ConflictStatus.AUTO_RESOLVED

    older_after = await fact_repo.get_by_id(older.id)
    newer_after = await fact_repo.get_by_id(newer.id)
    assert older_after is not None and older_after.valid_until is not None
    assert newer_after is not None and newer_after.conflict_status == "resolved"

    relations = await relation_repo.find_for_fact(newer.id)
    assert any(
        rel.relation_type == RelationType.SUPERSEDES
        and rel.source_fact_id == newer.id
        and rel.target_fact_id == older.id
        for rel in relations
    )


@pytest.mark.asyncio
async def test_conflict_resolution_uses_causal_order_when_authority_is_equal() -> None:
    fact_repo = InMemoryFactRepository()
    conflict_repo = InMemoryConflictRepository()
    relation_repo = InMemoryRelationRepository()

    fact_a = _make_fact(
        source_type=SourceType.AGENT_EXTRACTION,
        valid_from=datetime.now(UTC),
        metadata={"version_vector": {"agent-a": 1}},
    )
    fact_b = _make_fact(
        source_type=SourceType.AGENT_EXTRACTION,
        valid_from=datetime.now(UTC),
        metadata={"version_vector": {"agent-a": 2}},
    )
    await fact_repo.insert(fact_a)
    await fact_repo.insert(fact_b)

    conflict = Conflict(
        id=uuid4(),
        fact_a_id=fact_a.id,
        fact_b_id=fact_b.id,
        status=ConflictStatus.DETECTED,
    )
    await conflict_repo.insert(conflict)

    pipeline = ConflictResolutionPipeline(
        fact_repo=fact_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        config=MyceliumConfig(conflict_ambiguity_window_s=3600),
    )

    result = await pipeline.resolve_one(conflict)
    assert result.status == ConflictStatus.AUTO_RESOLVED
    assert result.winning_fact_id == fact_b.id
    assert result.resolution["reason"] == "causal_order"


@pytest.mark.asyncio
async def test_conflict_resolution_escalates_ambiguous_without_llm() -> None:
    fact_repo = InMemoryFactRepository()
    conflict_repo = InMemoryConflictRepository()
    relation_repo = InMemoryRelationRepository()

    base_time = datetime.now(UTC)
    fact_a = _make_fact(source_type=SourceType.AGENT_EXTRACTION, valid_from=base_time)
    fact_b = _make_fact(
        source_type=SourceType.AGENT_EXTRACTION,
        valid_from=base_time + timedelta(seconds=1),
    )
    await fact_repo.insert(fact_a)
    await fact_repo.insert(fact_b)

    conflict = Conflict(
        id=uuid4(),
        fact_a_id=fact_a.id,
        fact_b_id=fact_b.id,
        status=ConflictStatus.DETECTED,
    )
    await conflict_repo.insert(conflict)

    pipeline = ConflictResolutionPipeline(
        fact_repo=fact_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        config=MyceliumConfig(conflict_ambiguity_window_s=120),
    )

    result = await pipeline.resolve_one(conflict)
    assert result.status == ConflictStatus.ESCALATED
    assert result.winning_fact_id is None


@pytest.mark.asyncio
async def test_conflict_resolution_accepts_confident_llm_decision() -> None:
    fact_repo = InMemoryFactRepository()
    conflict_repo = InMemoryConflictRepository()
    relation_repo = InMemoryRelationRepository()

    base_time = datetime.now(UTC)
    fact_a = _make_fact(source_type=SourceType.AGENT_EXTRACTION, valid_from=base_time)
    fact_b = _make_fact(
        source_type=SourceType.AGENT_EXTRACTION,
        valid_from=base_time + timedelta(seconds=1),
    )
    await fact_repo.insert(fact_a)
    await fact_repo.insert(fact_b)

    conflict = Conflict(
        id=uuid4(),
        fact_a_id=fact_a.id,
        fact_b_id=fact_b.id,
        status=ConflictStatus.DETECTED,
    )
    await conflict_repo.insert(conflict)

    pipeline = ConflictResolutionPipeline(
        fact_repo=fact_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        config=MyceliumConfig(conflict_ambiguity_window_s=120, llm_resolution_min_confidence=0.7),
        llm_resolver=_FixedLLMResolver(fact_b.id, 0.91),
    )

    result = await pipeline.resolve_one(conflict)
    assert result.status == ConflictStatus.LLM_RESOLVED
    assert result.winning_fact_id == fact_b.id


@pytest.mark.asyncio
async def test_conflict_resolution_handles_three_agent_disagreement() -> None:
    fact_repo = InMemoryFactRepository()
    conflict_repo = InMemoryConflictRepository()
    relation_repo = InMemoryRelationRepository()

    t0 = datetime.now(UTC)
    fact_a = Fact(
        id=uuid4(),
        content=FactContent(subject="service-x", predicate="has_status", object="down"),
        source_agent_id="agent-a",
        source_type=SourceType.AGENT_EXTRACTION,
        confidence=0.6,
        trust_score=0.5,
        valid_from=t0,
    )
    fact_b = Fact(
        id=uuid4(),
        content=FactContent(subject="service-x", predicate="has_status", object="healthy"),
        source_agent_id="agent-b",
        source_type=SourceType.AGENT_EXTRACTION,
        confidence=0.6,
        trust_score=0.5,
        valid_from=t0 + timedelta(seconds=300),
    )
    fact_c = Fact(
        id=uuid4(),
        content=FactContent(subject="service-x", predicate="has_status", object="degraded"),
        source_agent_id="agent-c",
        source_type=SourceType.HUMAN_CORRECTION,
        confidence=1.0,
        trust_score=1.0,
        valid_from=t0 + timedelta(seconds=600),
    )
    for fact in (fact_a, fact_b, fact_c):
        await fact_repo.insert(fact)

    for left, right in ((fact_a, fact_b), (fact_a, fact_c), (fact_b, fact_c)):
        await conflict_repo.insert(
            Conflict(
                id=uuid4(),
                fact_a_id=left.id,
                fact_b_id=right.id,
                status=ConflictStatus.DETECTED,
            )
        )

    pipeline = ConflictResolutionPipeline(
        fact_repo=fact_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        config=MyceliumConfig(conflict_ambiguity_window_s=120),
    )
    resolved = await pipeline.resolve_pending()
    assert len(resolved) == 3
    assert all(result.status != ConflictStatus.DETECTED for result in resolved)


@pytest.mark.asyncio
async def test_provenance_pipeline_traces_derived_from_chain() -> None:
    fact_repo = InMemoryFactRepository()
    relation_repo = InMemoryRelationRepository()

    root = Fact(
        id=uuid4(),
        content=FactContent(subject="api", predicate="version_is", object="v1"),
        source_agent_id="agent-a",
        source_type=SourceType.AGENT_EXTRACTION,
        confidence=0.6,
        trust_score=0.5,
        valid_from=datetime.now(UTC),
    )
    child = Fact(
        id=uuid4(),
        content=FactContent(subject="api", predicate="version_is", object="v2"),
        source_agent_id="agent-b",
        source_type=SourceType.HUMAN_CORRECTION,
        confidence=0.8,
        trust_score=0.8,
        valid_from=datetime.now(UTC),
        derived_from=[root.id],
        supersedes=root.id,
    )
    await fact_repo.insert(root)
    await fact_repo.insert(child)

    pipeline = ProvenancePipeline(fact_repo=fact_repo, relation_repo=relation_repo)
    chain = await pipeline.trace_chain(child.id)

    assert [entry.fact.id for entry in chain] == [child.id, root.id]
    assert [entry.depth for entry in chain] == [0, 1]


@pytest.mark.asyncio
async def test_ingest_assigns_version_vector_metadata() -> None:
    fact_repo = InMemoryFactRepository()
    agent_repo = InMemoryAgentRepository()
    conflict_repo = InMemoryConflictRepository()
    relation_repo = InMemoryRelationRepository()

    await agent_repo.upsert(AgentRecord(id="agent-a", role="test"))

    pipeline = IngestPipeline(
        fact_repo=fact_repo,
        agent_repo=agent_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        embedding_provider=MockEmbeddingProvider(dimension=64),
        config=MyceliumConfig(),
    )

    first = await pipeline.ingest(
        content=FactContent(subject="api", predicate="has_status", object="healthy"),
        source_agent_id="agent-a",
        source_type=SourceType.AGENT_EXTRACTION,
    )
    second = await pipeline.ingest(
        content=FactContent(subject="api", predicate="has_status", object="degraded"),
        source_agent_id="agent-a",
        source_type=SourceType.AGENT_EXTRACTION,
    )

    assert first.fact is not None
    assert second.fact is not None

    vec_first = first.fact.metadata.get("version_vector")
    vec_second = second.fact.metadata.get("version_vector")

    assert isinstance(vec_first, dict)
    assert isinstance(vec_second, dict)
    vec_first_typed = cast("dict[str, int]", vec_first)
    vec_second_typed = cast("dict[str, int]", vec_second)
    assert vec_first_typed.get("agent-a") == 1
    assert vec_second_typed.get("agent-a") == 2
