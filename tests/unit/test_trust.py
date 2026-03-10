"""Unit tests for trust scoring."""

from mycelium.domain.trust import (
    compute_agent_contradiction_rate,
    compute_initial_confidence,
    compute_trust_score,
)
from mycelium.domain.types import AgentRecord, SourceType


class TestComputeInitialConfidence:
    def test_human_correction(self) -> None:
        assert compute_initial_confidence(SourceType.HUMAN_CORRECTION) == 1.0

    def test_system_verification(self) -> None:
        assert compute_initial_confidence(SourceType.SYSTEM_VERIFICATION) == 0.85

    def test_agent_extraction(self) -> None:
        assert compute_initial_confidence(SourceType.AGENT_EXTRACTION) == 0.6

    def test_agent_inference(self) -> None:
        assert compute_initial_confidence(SourceType.AGENT_INFERENCE) == 0.4

    def test_hierarchy_preserved(self) -> None:
        scores = [compute_initial_confidence(st) for st in SourceType]
        assert scores == sorted(scores, reverse=True)


class TestComputeTrustScore:
    def test_no_agent_no_corroboration(self) -> None:
        score = compute_trust_score(SourceType.AGENT_EXTRACTION)
        assert score == 0.6

    def test_human_correction_always_high(self) -> None:
        score = compute_trust_score(SourceType.HUMAN_CORRECTION)
        assert score == 1.0

    def test_agent_history_lowers_score(self) -> None:
        bad_agent = AgentRecord(id="bad", role="test", trust_score=0.2)
        score = compute_trust_score(SourceType.AGENT_EXTRACTION, agent=bad_agent)
        base_score = compute_trust_score(SourceType.AGENT_EXTRACTION)
        assert score < base_score

    def test_agent_history_raises_score(self) -> None:
        good_agent = AgentRecord(id="good", role="test", trust_score=1.0)
        score = compute_trust_score(SourceType.AGENT_EXTRACTION, agent=good_agent)
        base_score = compute_trust_score(SourceType.AGENT_EXTRACTION)
        assert score >= base_score

    def test_corroboration_boosts_score(self) -> None:
        base = compute_trust_score(SourceType.AGENT_INFERENCE)
        boosted = compute_trust_score(SourceType.AGENT_INFERENCE, corroboration_count=5)
        assert boosted > base

    def test_corroboration_diminishing_returns(self) -> None:
        boost_1 = (
            compute_trust_score(SourceType.AGENT_INFERENCE, corroboration_count=1)
            - compute_trust_score(SourceType.AGENT_INFERENCE)
        )
        boost_10 = (
            compute_trust_score(SourceType.AGENT_INFERENCE, corroboration_count=10)
            - compute_trust_score(SourceType.AGENT_INFERENCE, corroboration_count=9)
        )
        assert boost_1 > boost_10  # diminishing returns

    def test_score_bounded(self) -> None:
        score = compute_trust_score(
            SourceType.HUMAN_CORRECTION,
            agent=AgentRecord(id="x", role="test", trust_score=1.0),
            corroboration_count=100,
        )
        assert 0.0 <= score <= 1.0


class TestComputeAgentContradictionRate:
    def test_no_facts(self) -> None:
        agent = AgentRecord(id="x", role="test", facts_contributed=0)
        assert compute_agent_contradiction_rate(agent) == 0.0

    def test_some_contradictions(self) -> None:
        agent = AgentRecord(
            id="x", role="test", facts_contributed=100, facts_contradicted=15
        )
        assert compute_agent_contradiction_rate(agent) == 0.15

    def test_no_contradictions(self) -> None:
        agent = AgentRecord(
            id="x", role="test", facts_contributed=50, facts_contradicted=0
        )
        assert compute_agent_contradiction_rate(agent) == 0.0
