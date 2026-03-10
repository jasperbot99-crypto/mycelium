"""Tests for LLM error fallback behavior in conflict resolution."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from mycelium.config import MyceliumConfig
from mycelium.domain.types import Conflict, ConflictStatus, Fact, FactContent, SourceType
from mycelium.pipelines.conflict_resolution import ConflictResolutionPipeline, LLMResolutionDecision
from mycelium.storage.memory import (
    InMemoryConflictRepository,
    InMemoryFactRepository,
    InMemoryRelationRepository,
)


class _FailingResolver:
    async def resolve(
        self,
        conflict: Conflict,
        fact_a: Fact,
        fact_b: Fact,
    ) -> LLMResolutionDecision:
        del conflict, fact_a, fact_b
        raise RuntimeError("provider down")


def _fact(obj: str) -> Fact:
    return Fact(
        id=uuid4(),
        content=FactContent(subject="api", predicate="has_status", object=obj),
        source_agent_id="agent-a",
        source_type=SourceType.AGENT_EXTRACTION,
        confidence=0.6,
        trust_score=0.5,
        valid_from=datetime.now(),
    )


@pytest.mark.asyncio
async def test_llm_error_escalates_conflict() -> None:
    fact_repo = InMemoryFactRepository()
    conflict_repo = InMemoryConflictRepository()
    relation_repo = InMemoryRelationRepository()

    fact_a = _fact("healthy")
    fact_b = _fact("degraded")
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
        llm_resolver=_FailingResolver(),
    )

    result = await pipeline.resolve_one(conflict)
    assert result.status == ConflictStatus.ESCALATED
    assert result.resolution["reason"] == "llm_error"
