"""Phase 4 conflict resolution pipeline.

Resolution order:
1) Deterministic resolution (source authority, then causal order, then temporal order)
2) LLM-assisted resolution for ambiguous conflicts
3) Escalation when no reliable winner can be selected
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid4

from mycelium.domain.consistency import (
    CausalOrder,
    compare_vectors,
    normalize_vector,
)
from mycelium.domain.types import (
    Conflict,
    ConflictResolutionResult,
    ConflictStatus,
    Fact,
    FactRelation,
    RelationType,
)
from mycelium.ops.logger import NullOpsLogger, OpsLogger, ms_since

if TYPE_CHECKING:
    from mycelium.config import MyceliumConfig
    from mycelium.storage.protocols import (
        ConflictRepository,
        FactRepository,
        RelationRepository,
    )


@dataclass(frozen=True)
class LLMResolutionDecision:
    """Decision produced by an LLM conflict resolver."""

    winning_fact_id: UUID | None
    confidence: float
    rationale: str
    escalate: bool = False


class ConflictLLMResolver(Protocol):
    """Protocol for LLM-assisted conflict resolution."""

    async def resolve(
        self, conflict: Conflict, fact_a: Fact, fact_b: Fact,
    ) -> LLMResolutionDecision:
        """Resolve an ambiguous conflict and return a decision."""
        ...


class ConflictResolutionPipeline:
    """Resolve detected conflicts with deterministic + LLM-assisted strategy."""

    def __init__(
        self,
        fact_repo: FactRepository,
        conflict_repo: ConflictRepository,
        relation_repo: RelationRepository,
        config: MyceliumConfig,
        llm_resolver: ConflictLLMResolver | None = None,
        ops_logger: OpsLogger | None = None,
    ) -> None:
        self._fact_repo = fact_repo
        self._conflict_repo = conflict_repo
        self._relation_repo = relation_repo
        self._config = config
        self._llm_resolver = llm_resolver
        self._ops = ops_logger or NullOpsLogger()

    async def resolve_pending(self, limit: int = 100) -> list[ConflictResolutionResult]:
        """Resolve detected conflicts, up to `limit` records."""
        conflicts = await self._conflict_repo.find_by_status(ConflictStatus.DETECTED)
        resolved: list[ConflictResolutionResult] = []

        for conflict in conflicts[:limit]:
            resolved.append(await self.resolve_one(conflict))

        return resolved

    async def resolve_one(self, conflict: Conflict) -> ConflictResolutionResult:
        """Resolve a single conflict record."""
        t0 = time.monotonic()

        fact_a = await self._fact_repo.get_by_id(conflict.fact_a_id)
        fact_b = await self._fact_repo.get_by_id(conflict.fact_b_id)

        if fact_a is None or fact_b is None:
            missing_reason = "missing_fact_reference"
            result = await self._finalize_resolution(
                conflict=conflict,
                status=ConflictStatus.ESCALATED,
                winning_fact_id=None,
                resolved_by="system:conflict_resolution",
                resolution={"reason": missing_reason},
            )
            await self._ops.log(
                "conflict_resolution",
                "escalated",
                fact_id=conflict.fact_a_id,
                latency_ms=ms_since(t0),
                detail={"conflict_id": str(conflict.id), "reason": missing_reason},
            )
            return result

        deterministic = self._deterministic_winner(fact_a, fact_b)
        if deterministic is not None:
            winner_id, reason, detail = deterministic
            result = await self._finalize_resolution(
                conflict=conflict,
                status=ConflictStatus.AUTO_RESOLVED,
                winning_fact_id=winner_id,
                resolved_by="system:deterministic",
                resolution={"reason": reason, **detail},
            )
            await self._ops.log(
                "conflict_resolution",
                "auto_resolved",
                fact_id=winner_id,
                latency_ms=ms_since(t0),
                detail={"conflict_id": str(conflict.id), "reason": reason},
            )
            return result

        if self._llm_resolver is None:
            result = await self._finalize_resolution(
                conflict=conflict,
                status=ConflictStatus.ESCALATED,
                winning_fact_id=None,
                resolved_by="system:conflict_resolution",
                resolution={"reason": "ambiguous_without_llm"},
            )
            await self._ops.log(
                "conflict_resolution",
                "escalated",
                fact_id=conflict.fact_a_id,
                latency_ms=ms_since(t0),
                detail={
                    "conflict_id": str(conflict.id),
                    "reason": "ambiguous_without_llm",
                },
            )
            return result

        try:
            llm_decision = await self._llm_resolver.resolve(conflict, fact_a, fact_b)
        except Exception as exc:
            result = await self._finalize_resolution(
                conflict=conflict,
                status=ConflictStatus.ESCALATED,
                winning_fact_id=None,
                resolved_by="llm",
                resolution={
                    "reason": "llm_error",
                    "error": str(exc),
                },
            )
            await self._ops.log(
                "conflict_resolution",
                "escalated",
                fact_id=conflict.fact_a_id,
                latency_ms=ms_since(t0),
                detail={
                    "conflict_id": str(conflict.id),
                    "reason": "llm_error",
                },
            )
            return result

        llm_confident = llm_decision.confidence >= self._config.llm_resolution_min_confidence
        llm_selected_winner = llm_decision.winning_fact_id in {fact_a.id, fact_b.id}

        if llm_decision.escalate or not llm_confident or not llm_selected_winner:
            result = await self._finalize_resolution(
                conflict=conflict,
                status=ConflictStatus.ESCALATED,
                winning_fact_id=None,
                resolved_by="llm",
                resolution={
                    "reason": "llm_low_confidence",
                    "llm_confidence": llm_decision.confidence,
                    "llm_rationale": llm_decision.rationale,
                },
            )
            await self._ops.log(
                "conflict_resolution",
                "escalated",
                fact_id=conflict.fact_a_id,
                latency_ms=ms_since(t0),
                detail={
                    "conflict_id": str(conflict.id),
                    "reason": "llm_low_confidence",
                    "confidence": llm_decision.confidence,
                },
            )
            return result

        result = await self._finalize_resolution(
            conflict=conflict,
            status=ConflictStatus.LLM_RESOLVED,
            winning_fact_id=llm_decision.winning_fact_id,
            resolved_by="llm",
            resolution={
                "reason": "llm_resolution",
                "llm_confidence": llm_decision.confidence,
                "llm_rationale": llm_decision.rationale,
            },
        )
        assert llm_decision.winning_fact_id is not None
        await self._ops.log(
            "conflict_resolution",
            "llm_resolved",
            fact_id=llm_decision.winning_fact_id,
            latency_ms=ms_since(t0),
            detail={
                "conflict_id": str(conflict.id),
                "confidence": llm_decision.confidence,
            },
        )
        return result

    def _deterministic_winner(
        self,
        fact_a: Fact,
        fact_b: Fact,
    ) -> tuple[UUID, str, dict[str, object]] | None:
        weight_a = fact_a.source_type.trust_weight
        weight_b = fact_b.source_type.trust_weight

        if weight_a > weight_b:
            return fact_a.id, "higher_source_authority", {
                "winner_weight": weight_a,
                "loser_weight": weight_b,
            }
        if weight_b > weight_a:
            return fact_b.id, "higher_source_authority", {
                "winner_weight": weight_b,
                "loser_weight": weight_a,
            }

        vec_a = normalize_vector(fact_a.metadata.get("version_vector"))
        vec_b = normalize_vector(fact_b.metadata.get("version_vector"))
        if vec_a or vec_b:
            ordering = compare_vectors(vec_a, vec_b)
            if ordering == CausalOrder.BEFORE:
                return fact_b.id, "causal_order", {"causal_order": ordering.value}
            if ordering == CausalOrder.AFTER:
                return fact_a.id, "causal_order", {"causal_order": ordering.value}

        delta_seconds = abs((fact_a.valid_from - fact_b.valid_from).total_seconds())
        if delta_seconds > self._config.conflict_ambiguity_window_s:
            if fact_a.valid_from > fact_b.valid_from:
                return fact_a.id, "temporal_order", {"time_delta_s": round(delta_seconds, 3)}
            return fact_b.id, "temporal_order", {"time_delta_s": round(delta_seconds, 3)}

        return None

    async def _finalize_resolution(
        self,
        conflict: Conflict,
        status: ConflictStatus,
        winning_fact_id: UUID | None,
        resolved_by: str,
        resolution: dict[str, object],
    ) -> ConflictResolutionResult:
        await self._conflict_repo.update_resolution(
            conflict.id,
            status=status,
            winning_fact_id=winning_fact_id,
            resolved_by=resolved_by,
            resolution=resolution,
        )

        if status in {ConflictStatus.AUTO_RESOLVED, ConflictStatus.LLM_RESOLVED}:
            assert winning_fact_id is not None
            losing_fact_id = (
                conflict.fact_b_id if winning_fact_id == conflict.fact_a_id else conflict.fact_a_id
            )

            await self._fact_repo.update_conflict_status(winning_fact_id, "resolved")
            await self._fact_repo.update_conflict_status(losing_fact_id, "resolved")

            losing_fact = await self._fact_repo.get_by_id(losing_fact_id)
            if losing_fact is not None and losing_fact.is_active:
                await self._fact_repo.set_valid_until(losing_fact_id, datetime.now())

            await self._ensure_supersedes_relation(winning_fact_id, losing_fact_id)

        return ConflictResolutionResult(
            conflict_id=conflict.id,
            status=status,
            winning_fact_id=winning_fact_id,
            resolved_by=resolved_by,
            resolution=resolution,
        )

    async def _ensure_supersedes_relation(
        self,
        winner_fact_id: UUID,
        loser_fact_id: UUID,
    ) -> None:
        existing = await self._relation_repo.find_for_fact(winner_fact_id)
        relation_exists = any(
            relation.relation_type == RelationType.SUPERSEDES
            and relation.source_fact_id == winner_fact_id
            and relation.target_fact_id == loser_fact_id
            for relation in existing
        )
        if relation_exists:
            return

        relation = FactRelation(
            id=uuid4(),
            source_fact_id=winner_fact_id,
            target_fact_id=loser_fact_id,
            relation_type=RelationType.SUPERSEDES,
            created_by="system:conflict_resolution",
        )
        await self._relation_repo.insert(relation)
