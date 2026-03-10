"""Hallucination detection — lightweight pre-store checks (Phase 3).

No LLM required. Catches implausible facts at ingest time using:
- Source plausibility: agent_inference with very high confidence is suspicious
- Content heuristics: empty-ish content, self-referential subjects
- Confidence/source mismatch: low-trust agent claiming high confidence

Returns a HallucinationCheckResult. If flagged, the fact can be rejected or
ingested with reduced confidence (configurable).
"""

from __future__ import annotations

from dataclasses import dataclass

from mycelium.domain.types import AgentRecord, FactContent, RejectionReason, SourceType


@dataclass(frozen=True)
class HallucinationFlag:
    """A single hallucination signal."""

    code: str
    message: str
    confidence_penalty: float  # how much to reduce confidence [0.0, 1.0]


@dataclass
class HallucinationCheckResult:
    """Result of running hallucination checks on a candidate fact."""

    flags: list[HallucinationFlag]

    @property
    def flagged(self) -> bool:
        return len(self.flags) > 0

    @property
    def total_penalty(self) -> float:
        """Sum of all penalties, clamped to [0.0, 1.0]."""
        return min(1.0, sum(f.confidence_penalty for f in self.flags))

    def to_rejection(self) -> RejectionReason:
        """Convert to a RejectionReason if the fact should be blocked."""
        codes = ", ".join(f.code for f in self.flags)
        messages = "; ".join(f.message for f in self.flags)
        return RejectionReason(
            code=f"hallucination:{codes}",
            message=messages,
        )


@dataclass
class HallucinationConfig:
    """Tuning knobs for hallucination detection."""

    reject_threshold: float = 0.8
    """Total penalty above this → reject the fact outright."""

    inference_max_confidence: float = 0.85
    """agent_inference facts claiming higher confidence than this are penalized."""

    low_trust_agent_threshold: float = 0.25
    """Agents with trust below this get extra scrutiny."""

    min_content_length: int = 2
    """Minimum character length for subject/predicate/object (after strip)."""


def check_hallucination(
    content: FactContent,
    source_type: SourceType,
    agent: AgentRecord | None,
    claimed_confidence: float | None,
    config: HallucinationConfig | None = None,
) -> HallucinationCheckResult:
    """Run all hallucination checks on a candidate fact.

    Pure function — no I/O, no side effects.
    """
    cfg = config or HallucinationConfig()
    flags: list[HallucinationFlag] = []

    # 1. Content length check
    for field_name, value in [
        ("subject", content.subject),
        ("predicate", content.predicate),
        ("object", content.object),
    ]:
        if len(value.strip()) < cfg.min_content_length:
            flags.append(HallucinationFlag(
                code=f"short_{field_name}",
                message=f"{field_name} is suspiciously short: '{value}'",
                confidence_penalty=0.3,
            ))

    # 2. Self-referential check: subject == object (exact match)
    if content.subject.strip().lower() == content.object.strip().lower():
        flags.append(HallucinationFlag(
            code="self_referential",
            message=f"subject equals object: '{content.subject}'",
            confidence_penalty=0.4,
        ))

    # 3. Inference with inflated confidence
    if (
        source_type == SourceType.AGENT_INFERENCE
        and claimed_confidence is not None
        and claimed_confidence > cfg.inference_max_confidence
    ):
        flags.append(HallucinationFlag(
            code="inflated_confidence",
            message=f"agent_inference claiming confidence={claimed_confidence:.2f} (max {cfg.inference_max_confidence})",
            confidence_penalty=0.3,
        ))

    # 4. Low-trust agent making high-confidence claims
    if (
        agent is not None
        and agent.trust_score < cfg.low_trust_agent_threshold
        and claimed_confidence is not None
        and claimed_confidence > 0.7
    ):
        flags.append(HallucinationFlag(
            code="low_trust_high_confidence",
            message=f"agent trust={agent.trust_score:.2f} but claiming confidence={claimed_confidence:.2f}",
            confidence_penalty=0.25,
        ))

    return HallucinationCheckResult(flags=flags)
