"""Verification pipeline — Phase 3 ground-truth checks and trust adjustments."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from mycelium.domain.types import VerificationMethod, VerificationResult, VerificationStatus
from mycelium.ops.logger import NullOpsLogger, OpsLogger

if TYPE_CHECKING:
    from uuid import UUID

    from mycelium.storage.protocols import AgentRepository, FactRepository

_FACT_DELTAS: dict[VerificationStatus, tuple[float, float]] = {
    VerificationStatus.VERIFIED: (0.1, 0.05),
    VerificationStatus.FAILED: (-0.25, -0.15),
    VerificationStatus.STALE: (-0.1, -0.05),
    VerificationStatus.UNVERIFIED: (0.0, 0.0),
}

_AGENT_TRUST_DELTAS: dict[VerificationStatus, float] = {
    VerificationStatus.VERIFIED: 0.02,
    VerificationStatus.FAILED: -0.05,
    VerificationStatus.STALE: -0.01,
    VerificationStatus.UNVERIFIED: 0.0,
}


class VerificationPipeline:
    """Apply verification outcomes to fact quality and source agent trust history."""

    def __init__(
        self,
        fact_repo: FactRepository,
        agent_repo: AgentRepository,
        ops_logger: OpsLogger | None = None,
    ) -> None:
        self._fact_repo = fact_repo
        self._agent_repo = agent_repo
        self._ops = ops_logger or NullOpsLogger()

    async def verify(
        self,
        fact_id: UUID,
        method: VerificationMethod,
        status: VerificationStatus,
        reason: str | None = None,
    ) -> VerificationResult:
        fact = await self._fact_repo.get_by_id(fact_id)
        if fact is None:
            raise ValueError(f"fact not found: {fact_id}")

        previous_status = fact.verification_status
        confidence_delta, trust_delta = _FACT_DELTAS[status]
        verified_at = datetime.now()

        next_confidence = round(min(1.0, max(0.0, fact.confidence + confidence_delta)), 4)
        next_trust = round(min(1.0, max(0.0, fact.trust_score + trust_delta)), 4)

        await self._fact_repo.update_scores(
            fact.id,
            confidence=next_confidence,
            trust_score=next_trust,
        )
        await self._fact_repo.update_verification(
            fact.id,
            status=status,
            verified_at=verified_at,
        )

        agent_trust_delta = _AGENT_TRUST_DELTAS[status]
        if agent_trust_delta != 0.0:
            await self._agent_repo.update_trust_stats(
                fact.source_agent_id,
                trust_score_delta=agent_trust_delta,
            )

        await self._ops.log(
            "verify",
            status.value,
            fact_id=fact.id,
            agent_id=fact.source_agent_id,
            detail={
                "method": method.value,
                "previous_status": previous_status.value,
                "confidence_delta": confidence_delta,
                "trust_delta": trust_delta,
                "reason": reason,
            },
        )

        return VerificationResult(
            fact_id=fact.id,
            status=status,
            method=method,
            previous_status=previous_status,
            verified_at=verified_at,
            confidence_delta=confidence_delta,
            trust_delta=trust_delta,
            reason=reason,
        )
