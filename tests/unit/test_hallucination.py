"""Tests for hallucination detection — lightweight pre-store checks."""

from __future__ import annotations

import pytest

from mycelium.domain.types import AgentRecord, FactContent, SourceType
from mycelium.pipelines.hallucination import (
    HallucinationConfig,
    check_hallucination,
)


@pytest.fixture
def config() -> HallucinationConfig:
    return HallucinationConfig()


def _agent(trust_score: float = 0.5) -> AgentRecord:
    return AgentRecord(id="test-agent", role="generic", trust_score=trust_score)


class TestHallucinationDetection:
    def test_normal_fact_passes(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(),
            claimed_confidence=None,
            config=config,
        )
        assert not result.flagged
        assert result.total_penalty == 0.0

    def test_short_subject_flagged(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="x", predicate="has_status", object="healthy")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(),
            claimed_confidence=None,
            config=config,
        )
        assert result.flagged
        assert any(f.code == "short_subject" for f in result.flags)

    def test_short_predicate_flagged(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="api-orders", predicate="x", object="healthy")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(),
            claimed_confidence=None,
            config=config,
        )
        assert result.flagged
        assert any(f.code == "short_predicate" for f in result.flags)

    def test_short_object_flagged(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="api-orders", predicate="has_status", object="x")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(),
            claimed_confidence=None,
            config=config,
        )
        assert result.flagged
        assert any(f.code == "short_object" for f in result.flags)

    def test_self_referential_flagged(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="api-orders", predicate="is_same_as", object="api-orders")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(),
            claimed_confidence=None,
            config=config,
        )
        assert result.flagged
        assert any(f.code == "self_referential" for f in result.flags)

    def test_self_referential_case_insensitive(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="Api-Orders", predicate="is_same_as", object="api-orders")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(),
            claimed_confidence=None,
            config=config,
        )
        assert any(f.code == "self_referential" for f in result.flags)

    def test_inflated_confidence_for_inference(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_INFERENCE,
            agent=_agent(),
            claimed_confidence=0.95,
            config=config,
        )
        assert result.flagged
        assert any(f.code == "inflated_confidence" for f in result.flags)

    def test_extraction_high_confidence_not_flagged(self, config: HallucinationConfig) -> None:
        """Only agent_inference gets the inflated confidence check."""
        content = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(),
            claimed_confidence=0.95,
            config=config,
        )
        assert not result.flagged

    def test_low_trust_high_confidence_flagged(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(trust_score=0.1),
            claimed_confidence=0.9,
            config=config,
        )
        assert result.flagged
        assert any(f.code == "low_trust_high_confidence" for f in result.flags)

    def test_low_trust_low_confidence_not_flagged(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(trust_score=0.1),
            claimed_confidence=0.3,
            config=config,
        )
        assert not result.flagged

    def test_no_agent_skips_trust_check(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="api-orders", predicate="has_status", object="healthy")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=None,
            claimed_confidence=0.9,
            config=config,
        )
        assert not result.flagged

    def test_multiple_flags_accumulate_penalty(self, config: HallucinationConfig) -> None:
        """Short subject + self-referential + low trust = high penalty."""
        content = FactContent(subject="x", predicate="is_same_as", object="x")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_INFERENCE,
            agent=_agent(trust_score=0.1),
            claimed_confidence=0.95,
            config=config,
        )
        assert len(result.flags) >= 3
        assert result.total_penalty > 0.5

    def test_total_penalty_clamped_to_1(self, config: HallucinationConfig) -> None:
        """Even with many flags, total_penalty never exceeds 1.0."""
        content = FactContent(subject="x", predicate="y", object="x")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_INFERENCE,
            agent=_agent(trust_score=0.05),
            claimed_confidence=0.99,
            config=config,
        )
        assert result.total_penalty <= 1.0

    def test_to_rejection(self, config: HallucinationConfig) -> None:
        content = FactContent(subject="x", predicate="has_status", object="healthy")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(),
            claimed_confidence=None,
            config=config,
        )
        rejection = result.to_rejection()
        assert "hallucination" in rejection.code
        assert "short" in rejection.message.lower()

    def test_custom_config_thresholds(self) -> None:
        """Custom min_content_length changes what's considered short."""
        config = HallucinationConfig(min_content_length=5)
        content = FactContent(subject="abc", predicate="has_status", object="healthy")
        result = check_hallucination(
            content=content,
            source_type=SourceType.AGENT_EXTRACTION,
            agent=_agent(),
            claimed_confidence=None,
            config=config,
        )
        assert result.flagged
        assert any(f.code == "short_subject" for f in result.flags)
