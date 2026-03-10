"""Tests for pluggable verification providers."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from mycelium.domain.types import (
    Fact,
    FactContent,
    SourceType,
    VerificationMethod,
    VerificationStatus,
)
from mycelium.pipelines.verification_providers import (
    ConfidenceThresholdProvider,
    TemporalConsistencyProvider,
    VerificationProvider,
)


def _make_fact(
    confidence: float = 0.6,
    trust_score: float = 0.5,
    valid_from: datetime | None = None,
) -> Fact:
    return Fact(
        id=uuid4(),
        content=FactContent(subject="api-orders", predicate="has_status", object="healthy"),
        source_agent_id="test-agent",
        source_type=SourceType.AGENT_EXTRACTION,
        confidence=confidence,
        trust_score=trust_score,
        valid_from=valid_from or datetime.now(),
        created_at=datetime.now(),
    )


class TestConfidenceThresholdProvider:
    @pytest.mark.asyncio
    async def test_implements_protocol(self) -> None:
        provider = ConfidenceThresholdProvider()
        assert isinstance(provider, VerificationProvider)

    @pytest.mark.asyncio
    async def test_method_is_system_probe(self) -> None:
        provider = ConfidenceThresholdProvider()
        assert provider.method == VerificationMethod.SYSTEM_PROBE

    @pytest.mark.asyncio
    async def test_healthy_fact_verified(self) -> None:
        provider = ConfidenceThresholdProvider()
        fact = _make_fact(confidence=0.8, trust_score=0.7)
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.VERIFIED

    @pytest.mark.asyncio
    async def test_low_confidence_fails(self) -> None:
        provider = ConfidenceThresholdProvider(min_confidence=0.3)
        fact = _make_fact(confidence=0.1, trust_score=0.5)
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.FAILED
        assert "confidence" in (outcome.reason or "").lower()

    @pytest.mark.asyncio
    async def test_low_trust_fails(self) -> None:
        provider = ConfidenceThresholdProvider(min_trust_score=0.3)
        fact = _make_fact(confidence=0.5, trust_score=0.1)
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.FAILED

    @pytest.mark.asyncio
    async def test_below_stale_threshold(self) -> None:
        provider = ConfidenceThresholdProvider(stale_threshold=0.5)
        fact = _make_fact(confidence=0.4, trust_score=0.5)
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.STALE

    @pytest.mark.asyncio
    async def test_at_stale_threshold_verified(self) -> None:
        provider = ConfidenceThresholdProvider(stale_threshold=0.5)
        fact = _make_fact(confidence=0.5, trust_score=0.5)
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.VERIFIED

    @pytest.mark.asyncio
    async def test_custom_thresholds(self) -> None:
        provider = ConfidenceThresholdProvider(
            min_confidence=0.5,
            min_trust_score=0.5,
            stale_threshold=0.7,
        )
        # Above min but below stale → stale
        fact = _make_fact(confidence=0.6, trust_score=0.6)
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.STALE


class TestTemporalConsistencyProvider:
    @pytest.mark.asyncio
    async def test_implements_protocol(self) -> None:
        provider = TemporalConsistencyProvider()
        assert isinstance(provider, VerificationProvider)

    @pytest.mark.asyncio
    async def test_method_is_temporal_check(self) -> None:
        provider = TemporalConsistencyProvider()
        assert provider.method == VerificationMethod.TEMPORAL_CHECK

    @pytest.mark.asyncio
    async def test_recent_fact_verified(self) -> None:
        provider = TemporalConsistencyProvider()
        fact = _make_fact(valid_from=datetime.now() - timedelta(days=5))
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.VERIFIED

    @pytest.mark.asyncio
    async def test_old_fact_stale(self) -> None:
        provider = TemporalConsistencyProvider(max_age_days=90)
        fact = _make_fact(valid_from=datetime.now() - timedelta(days=120))
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.STALE

    @pytest.mark.asyncio
    async def test_future_fact_fails(self) -> None:
        provider = TemporalConsistencyProvider()
        fact = _make_fact(valid_from=datetime.now() + timedelta(days=5))
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.FAILED
        assert "future" in (outcome.reason or "").lower()

    @pytest.mark.asyncio
    async def test_slightly_future_ok(self) -> None:
        """Facts within 1 hour tolerance are fine (clock skew)."""
        provider = TemporalConsistencyProvider()
        fact = _make_fact(valid_from=datetime.now() + timedelta(minutes=30))
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.VERIFIED

    @pytest.mark.asyncio
    async def test_custom_max_age(self) -> None:
        provider = TemporalConsistencyProvider(max_age_days=30)
        fact = _make_fact(valid_from=datetime.now() - timedelta(days=45))
        outcome = await provider.check(fact)
        assert outcome.status == VerificationStatus.STALE

    @pytest.mark.asyncio
    async def test_outcome_has_reason(self) -> None:
        provider = TemporalConsistencyProvider()
        fact = _make_fact()
        outcome = await provider.check(fact)
        assert outcome.reason is not None
