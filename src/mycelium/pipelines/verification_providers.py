"""Pluggable verification providers — Phase 3.

Protocol for ground-truth verification + concrete adapters.
VerificationPipeline can delegate the actual check to any provider
that implements this protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mycelium.domain.types import Fact, VerificationMethod, VerificationStatus


@dataclass(frozen=True)
class VerificationOutcome:
    """Result from a verification provider."""

    status: VerificationStatus
    method: VerificationMethod
    reason: str | None = None


@runtime_checkable
class VerificationProvider(Protocol):
    """Pluggable interface for fact verification strategies.

    Each provider implements a specific verification method (system probe,
    temporal check, source re-check, etc.). The VerificationPipeline can
    run one or more providers and aggregate results.
    """

    @property
    def method(self) -> VerificationMethod:
        """Which verification method this provider implements."""
        ...

    async def check(self, fact: Fact) -> VerificationOutcome:
        """Run verification against a fact. Returns the outcome."""
        ...


class ConfidenceThresholdProvider:
    """Verifies facts based on confidence/trust thresholds.

    Simple provider that checks whether a fact's scores are above
    configurable thresholds. Useful as a baseline automated check.
    """

    def __init__(
        self,
        min_confidence: float = 0.3,
        min_trust_score: float = 0.2,
        stale_threshold: float = 0.5,
    ) -> None:
        self._min_confidence = min_confidence
        self._min_trust = min_trust_score
        self._stale_threshold = stale_threshold

    @property
    def method(self) -> VerificationMethod:
        return VerificationMethod.SYSTEM_PROBE

    async def check(self, fact: Fact) -> VerificationOutcome:
        if fact.confidence < self._min_confidence or fact.trust_score < self._min_trust:
            return VerificationOutcome(
                status=VerificationStatus.FAILED,
                method=self.method,
                reason=(
                    f"Below thresholds: confidence={fact.confidence:.2f}"
                    f" (min {self._min_confidence}),"
                    f" trust={fact.trust_score:.2f} (min {self._min_trust})"
                ),
            )

        if fact.confidence < self._stale_threshold:
            return VerificationOutcome(
                status=VerificationStatus.STALE,
                method=self.method,
                reason=(
                    f"Below stale threshold: confidence={fact.confidence:.2f}"
                    f" (threshold {self._stale_threshold})"
                ),
            )

        return VerificationOutcome(
            status=VerificationStatus.VERIFIED,
            method=self.method,
            reason=(
                f"Passed thresholds: confidence={fact.confidence:.2f},"
                f" trust={fact.trust_score:.2f}"
            ),
        )


class TemporalConsistencyProvider:
    """Verifies facts by checking temporal validity.

    Checks that the fact's valid_from is not in the future,
    and that the fact hasn't gone stale based on age.
    """

    def __init__(self, max_age_days: int = 180) -> None:
        self._max_age_days = max_age_days

    @property
    def method(self) -> VerificationMethod:
        return VerificationMethod.TEMPORAL_CHECK

    async def check(self, fact: Fact) -> VerificationOutcome:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)

        # Future facts are suspicious
        if fact.valid_from > now + timedelta(hours=1):
            return VerificationOutcome(
                status=VerificationStatus.FAILED,
                method=self.method,
                reason=f"valid_from is in the future: {fact.valid_from.isoformat()}",
            )

        # Very old facts go stale
        age = now - fact.valid_from
        if age > timedelta(days=self._max_age_days):
            return VerificationOutcome(
                status=VerificationStatus.STALE,
                method=self.method,
                reason=f"Fact is {age.days} days old (max {self._max_age_days})",
            )

        return VerificationOutcome(
            status=VerificationStatus.VERIFIED,
            method=self.method,
            reason=f"Temporal check passed: age={age.days} days",
        )
