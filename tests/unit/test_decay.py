"""Tests for DecayCycleRunner — periodic decay and garbage collection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mycelium.domain.types import (
    Fact,
    FactContent,
    SourceType,
    VerificationStatus,
)
from mycelium.pipelines.decay import DecayConfig, DecayCycleRunner
from mycelium.storage.memory import InMemoryFactRepository


@pytest.fixture
def fact_repo() -> InMemoryFactRepository:
    return InMemoryFactRepository()


@pytest.fixture
def config() -> DecayConfig:
    return DecayConfig(
        min_confidence=0.15,
        stale_days=90,
        expire_failed_verification=True,
    )


@pytest.fixture
def runner(fact_repo: InMemoryFactRepository, config: DecayConfig) -> DecayCycleRunner:
    return DecayCycleRunner(fact_repo=fact_repo, config=config)


def _make_fact(
    subject: str = "general-note",
    predicate: str = "has_status",
    tags: list[str] | None = None,
    metadata: dict[str, object] | None = None,
    confidence: float = 0.6,
    trust_score: float = 0.5,
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED,
    created_at: datetime | None = None,
    last_accessed_at: datetime | None = None,
    last_verified_at: datetime | None = None,
) -> Fact:
    return Fact(
        id=uuid4(),
        content=FactContent(subject=subject, predicate=predicate, object="healthy"),
        source_agent_id="test-agent",
        source_type=SourceType.AGENT_EXTRACTION,
        confidence=confidence,
        trust_score=trust_score,
        valid_from=created_at or datetime.now(UTC),
        created_at=created_at or datetime.now(UTC),
        verification_status=verification_status,
        last_accessed_at=last_accessed_at,
        last_verified_at=last_verified_at,
        tags=tags or [],
        metadata=metadata or {},
    )


class TestDecayCycleRunner:
    @pytest.mark.asyncio
    async def test_no_facts(self, runner: DecayCycleRunner) -> None:
        result = await runner.run_cycle()
        assert result.facts_scanned == 0
        assert result.expired_low_confidence == 0
        assert result.expired_failed_verification == 0
        assert result.expired_ttl == 0
        assert result.marked_stale == 0

    @pytest.mark.asyncio
    async def test_healthy_facts_untouched(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        fact = _make_fact(confidence=0.8, trust_score=0.7)
        await fact_repo.insert(fact)

        result = await runner.run_cycle()
        assert result.facts_scanned == 1
        assert result.expired_low_confidence == 0

        stored = await fact_repo.get_by_id(fact.id)
        assert stored is not None
        assert stored.is_active

    @pytest.mark.asyncio
    async def test_low_confidence_expired(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        fact = _make_fact(confidence=0.1)  # below 0.15 threshold
        await fact_repo.insert(fact)

        result = await runner.run_cycle()
        assert result.expired_low_confidence == 1

        stored = await fact_repo.get_by_id(fact.id)
        assert stored is not None
        assert not stored.is_active
        assert stored.expired_at is not None

    @pytest.mark.asyncio
    async def test_failed_verification_expired(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        fact = _make_fact(
            confidence=0.5,
            verification_status=VerificationStatus.FAILED,
        )
        await fact_repo.insert(fact)

        result = await runner.run_cycle()
        assert result.expired_failed_verification == 1

        stored = await fact_repo.get_by_id(fact.id)
        assert stored is not None
        assert not stored.is_active

    @pytest.mark.asyncio
    async def test_failed_verification_not_expired_when_disabled(
        self, fact_repo: InMemoryFactRepository
    ) -> None:
        config = DecayConfig(expire_failed_verification=False)
        runner = DecayCycleRunner(fact_repo=fact_repo, config=config)

        fact = _make_fact(
            confidence=0.5,
            verification_status=VerificationStatus.FAILED,
        )
        await fact_repo.insert(fact)

        result = await runner.run_cycle()
        assert result.expired_failed_verification == 0

    @pytest.mark.asyncio
    async def test_trading_fact_expires_on_ttl(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        created = datetime.now(UTC) - timedelta(hours=6)
        fact = _make_fact(
            subject="eurusd position",
            tags=["trading.positions"],
            created_at=created,
        )
        await fact_repo.insert(fact)

        result = await runner.run_cycle(now=datetime.now(UTC))
        assert result.expired_ttl == 1
        stored = await fact_repo.get_by_id(fact.id)
        assert stored is not None
        assert not stored.is_active

    @pytest.mark.asyncio
    async def test_service_status_expires_on_ttl(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        created = datetime.now(UTC) - timedelta(hours=30)
        fact = _make_fact(
            subject="api-orders",
            predicate="has_status",
            created_at=created,
        )
        await fact_repo.insert(fact)

        result = await runner.run_cycle(now=datetime.now(UTC))
        assert result.expired_ttl == 1
        stored = await fact_repo.get_by_id(fact.id)
        assert stored is not None
        assert not stored.is_active

    @pytest.mark.asyncio
    async def test_architecture_fact_has_no_ttl(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        created = datetime.now(UTC) - timedelta(days=7)
        fact = _make_fact(
            subject="architecture decision record",
            tags=["architecture"],
            created_at=created,
        )
        await fact_repo.insert(fact)

        result = await runner.run_cycle(now=datetime.now(UTC))
        assert result.expired_ttl == 0
        stored = await fact_repo.get_by_id(fact.id)
        assert stored is not None
        assert stored.is_active

    @pytest.mark.asyncio
    async def test_metadata_ttl_hours_overrides_heuristics(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        created = datetime.now(UTC) - timedelta(hours=3)
        fact = _make_fact(
            subject="architecture decision record",
            tags=["architecture"],
            metadata={"ttl_hours": 1},
            created_at=created,
        )
        await fact_repo.insert(fact)

        result = await runner.run_cycle(now=datetime.now(UTC))
        assert result.expired_ttl == 1

    @pytest.mark.asyncio
    async def test_stale_fact_marked(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        old_date = datetime.now(UTC) - timedelta(days=120)
        fact = _make_fact(confidence=0.5, created_at=old_date)
        await fact_repo.insert(fact)

        result = await runner.run_cycle()
        assert result.marked_stale == 1

        stored = await fact_repo.get_by_id(fact.id)
        assert stored is not None
        assert stored.verification_status == VerificationStatus.STALE
        assert stored.is_active  # stale != expired

    @pytest.mark.asyncio
    async def test_recently_accessed_not_stale(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        old_date = datetime.now(UTC) - timedelta(days=120)
        recent_access = datetime.now(UTC) - timedelta(days=5)
        fact = _make_fact(
            confidence=0.5,
            created_at=old_date,
            last_accessed_at=recent_access,
        )
        await fact_repo.insert(fact)

        result = await runner.run_cycle()
        assert result.marked_stale == 0

    @pytest.mark.asyncio
    async def test_already_stale_not_re_marked(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        old_date = datetime.now(UTC) - timedelta(days=120)
        fact = _make_fact(
            confidence=0.5,
            created_at=old_date,
            verification_status=VerificationStatus.STALE,
        )
        await fact_repo.insert(fact)

        result = await runner.run_cycle()
        assert result.marked_stale == 0  # already stale, skip

    @pytest.mark.asyncio
    async def test_already_expired_not_scanned(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        fact = _make_fact(confidence=0.1)
        fact.expired_at = datetime.now(UTC)  # already expired
        await fact_repo.insert(fact)

        result = await runner.run_cycle()
        assert result.facts_scanned == 0  # not active, not scanned

    @pytest.mark.asyncio
    async def test_low_confidence_takes_priority_over_stale(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        """Low confidence is checked first — fact is expired, not just marked stale."""
        old_date = datetime.now(UTC) - timedelta(days=120)
        fact = _make_fact(confidence=0.1, created_at=old_date)
        await fact_repo.insert(fact)

        result = await runner.run_cycle()
        assert result.expired_low_confidence == 1
        assert result.marked_stale == 0

    @pytest.mark.asyncio
    async def test_start_stop(self, runner: DecayCycleRunner) -> None:
        assert not runner.running
        await runner.start()
        assert runner.running
        await runner.stop()
        assert not runner.running

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, runner: DecayCycleRunner) -> None:
        await runner.start()
        await runner.start()
        assert runner.running
        await runner.stop()

    @pytest.mark.asyncio
    async def test_multiple_facts_mixed(
        self, runner: DecayCycleRunner, fact_repo: InMemoryFactRepository
    ) -> None:
        """Mix of healthy, low-confidence, failed, and stale facts."""
        old_date = datetime.now(UTC) - timedelta(days=120)

        await fact_repo.insert(_make_fact(confidence=0.8))  # healthy
        await fact_repo.insert(_make_fact(confidence=0.05))  # low confidence
        await fact_repo.insert(_make_fact(
            confidence=0.5, verification_status=VerificationStatus.FAILED
        ))  # failed
        await fact_repo.insert(_make_fact(confidence=0.5, created_at=old_date))  # stale

        result = await runner.run_cycle()
        assert result.facts_scanned == 4
        assert result.expired_low_confidence == 1
        assert result.expired_failed_verification == 1
        assert result.marked_stale == 1
