"""Tests for VerificationCycleRunner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mycelium.domain.types import (
    AgentRecord,
    Fact,
    FactContent,
    SourceType,
    VerificationMethod,
    VerificationStatus,
)
from mycelium.pipelines.verification import VerificationPipeline
from mycelium.pipelines.verification_cycle import (
    VerificationCycleConfig,
    VerificationCycleRunner,
)
from mycelium.pipelines.verification_providers import VerificationOutcome, VerificationProvider
from mycelium.storage.memory import InMemoryAgentRepository, InMemoryFactRepository


class _AlwaysVerifiedProvider:
    @property
    def method(self) -> VerificationMethod:
        return VerificationMethod.SYSTEM_PROBE

    async def check(self, fact: Fact) -> VerificationOutcome:
        del fact
        return VerificationOutcome(
            status=VerificationStatus.VERIFIED,
            method=self.method,
            reason="always verified",
        )


class _AlwaysFailedProvider:
    @property
    def method(self) -> VerificationMethod:
        return VerificationMethod.SOURCE_RECHECK

    async def check(self, fact: Fact) -> VerificationOutcome:
        del fact
        return VerificationOutcome(
            status=VerificationStatus.FAILED,
            method=self.method,
            reason="always failed",
        )


def _make_fact(
    *,
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED,
) -> Fact:
    now = datetime.now(UTC)
    return Fact(
        id=uuid4(),
        content=FactContent(subject="api-orders", predicate="has_status", object="healthy"),
        source_agent_id="agent-a",
        source_type=SourceType.AGENT_EXTRACTION,
        confidence=0.6,
        trust_score=0.5,
        valid_from=now - timedelta(days=1),
        created_at=now - timedelta(days=1),
        verification_status=verification_status,
    )


@pytest.fixture
def fact_repo() -> InMemoryFactRepository:
    return InMemoryFactRepository()


@pytest.fixture
def agent_repo() -> InMemoryAgentRepository:
    return InMemoryAgentRepository()


@pytest.mark.asyncio
async def test_cycle_verifies_unverified_facts(
    fact_repo: InMemoryFactRepository,
    agent_repo: InMemoryAgentRepository,
) -> None:
    await agent_repo.upsert(AgentRecord(id="agent-a", role="tester"))
    fact = _make_fact(verification_status=VerificationStatus.UNVERIFIED)
    await fact_repo.insert(fact)

    pipeline = VerificationPipeline(fact_repo=fact_repo, agent_repo=agent_repo)
    runner = VerificationCycleRunner(
        fact_repo=fact_repo,
        verification_pipeline=pipeline,
        providers=[_AlwaysVerifiedProvider()],
    )

    result = await runner.run_cycle()
    assert result.facts_scanned == 1
    assert result.facts_verified == 1
    assert result.facts_failed == 0

    updated = await fact_repo.get_by_id(fact.id)
    assert updated is not None
    assert updated.verification_status == VerificationStatus.VERIFIED


@pytest.mark.asyncio
async def test_cycle_prioritizes_failed_outcome(
    fact_repo: InMemoryFactRepository,
    agent_repo: InMemoryAgentRepository,
) -> None:
    await agent_repo.upsert(AgentRecord(id="agent-a", role="tester"))
    fact = _make_fact(verification_status=VerificationStatus.UNVERIFIED)
    await fact_repo.insert(fact)

    pipeline = VerificationPipeline(fact_repo=fact_repo, agent_repo=agent_repo)
    runner = VerificationCycleRunner(
        fact_repo=fact_repo,
        verification_pipeline=pipeline,
        providers=[_AlwaysVerifiedProvider(), _AlwaysFailedProvider()],
    )

    result = await runner.run_cycle()
    assert result.facts_failed == 1

    updated = await fact_repo.get_by_id(fact.id)
    assert updated is not None
    assert updated.verification_status == VerificationStatus.FAILED


@pytest.mark.asyncio
async def test_cycle_skips_already_verified(
    fact_repo: InMemoryFactRepository,
    agent_repo: InMemoryAgentRepository,
) -> None:
    await agent_repo.upsert(AgentRecord(id="agent-a", role="tester"))
    fact = _make_fact(verification_status=VerificationStatus.VERIFIED)
    await fact_repo.insert(fact)

    pipeline = VerificationPipeline(fact_repo=fact_repo, agent_repo=agent_repo)
    runner = VerificationCycleRunner(
        fact_repo=fact_repo,
        verification_pipeline=pipeline,
        providers=[_AlwaysVerifiedProvider()],
    )
    result = await runner.run_cycle()

    assert result.facts_scanned == 1
    assert result.facts_skipped == 1
    assert result.facts_verified == 0


@pytest.mark.asyncio
async def test_cycle_respects_max_facts_per_cycle(
    fact_repo: InMemoryFactRepository,
    agent_repo: InMemoryAgentRepository,
) -> None:
    await agent_repo.upsert(AgentRecord(id="agent-a", role="tester"))
    for _ in range(3):
        await fact_repo.insert(_make_fact(verification_status=VerificationStatus.UNVERIFIED))

    pipeline = VerificationPipeline(fact_repo=fact_repo, agent_repo=agent_repo)
    runner = VerificationCycleRunner(
        fact_repo=fact_repo,
        verification_pipeline=pipeline,
        providers=[_AlwaysVerifiedProvider()],
        config=VerificationCycleConfig(max_facts_per_cycle=2),
    )
    result = await runner.run_cycle()
    assert result.facts_scanned == 2
    assert result.facts_verified == 2


@pytest.mark.asyncio
async def test_start_stop(
    fact_repo: InMemoryFactRepository,
    agent_repo: InMemoryAgentRepository,
) -> None:
    pipeline = VerificationPipeline(fact_repo=fact_repo, agent_repo=agent_repo)
    runner = VerificationCycleRunner(
        fact_repo=fact_repo,
        verification_pipeline=pipeline,
        providers=[_AlwaysVerifiedProvider()],
        config=VerificationCycleConfig(cycle_interval_hours=24),
    )
    assert isinstance(_AlwaysVerifiedProvider(), VerificationProvider)
    assert runner.running is False
    await runner.start()
    assert runner.running is True
    await runner.stop()
    assert runner.running is False
