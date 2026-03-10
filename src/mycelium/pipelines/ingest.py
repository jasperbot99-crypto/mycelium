"""Ingest pipeline — ARCHITECTURE.md Section 4.1.

The most critical path. Every fact enters the system through this pipeline.

Flow: validate → hallucination check → resolve predicate → embed
→ contradiction check → score → store → propagate
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from mycelium.domain.conflict import ConflictCandidate, DetectionResult, detect_conflicts
from mycelium.domain.consistency import merge_vectors, next_vector, normalize_vector
from mycelium.domain.predicates import resolve_predicate
from mycelium.domain.trust import compute_initial_confidence, compute_trust_score
from mycelium.domain.types import (
    Conflict,
    ConflictStatus,
    Fact,
    FactContent,
    FactRelation,
    RejectionReason,
    RelationType,
    SourceType,
)
from mycelium.ops.logger import NullOpsLogger, OpsLogger, ms_since
from mycelium.pipelines.hallucination import (
    HallucinationConfig,
    check_hallucination,
)

if TYPE_CHECKING:
    from mycelium.config import MyceliumConfig
    from mycelium.embeddings.protocols import EmbeddingProvider
    from mycelium.pipelines.propagation import PropagationEngine
    from mycelium.storage.protocols import (
        AgentRepository,
        ConflictRepository,
        FactRepository,
        RelationRepository,
    )


class IngestResult:
    """Result of an ingest operation — either a stored Fact or a RejectionReason."""

    def __init__(
        self,
        fact: Fact | None = None,
        rejection: RejectionReason | None = None,
        conflicts: list[ConflictCandidate] | None = None,
        corroborations: list[ConflictCandidate] | None = None,
    ) -> None:
        self.fact = fact
        self.rejection = rejection
        self.conflicts = conflicts or []
        self.corroborations = corroborations or []

    @property
    def accepted(self) -> bool:
        return self.fact is not None

    @property
    def rejected(self) -> bool:
        return self.rejection is not None


class IngestPipeline:
    """Orchestrates the full ingest flow.

    validate → hallucination check → resolve predicate → embed → contradiction check → score → store

    Dependencies injected via constructor (Layer 3 depends on Layer 1 + Layer 2).
    """

    def __init__(
        self,
        fact_repo: FactRepository,
        agent_repo: AgentRepository,
        conflict_repo: ConflictRepository,
        relation_repo: RelationRepository,
        embedding_provider: EmbeddingProvider,
        config: MyceliumConfig,
        ops_logger: OpsLogger | None = None,
        propagation_engine: PropagationEngine | None = None,
        hallucination_config: HallucinationConfig | None = None,
    ) -> None:
        self._fact_repo = fact_repo
        self._agent_repo = agent_repo
        self._conflict_repo = conflict_repo
        self._relation_repo = relation_repo
        self._embedding = embedding_provider
        self._config = config
        self._ops = ops_logger or NullOpsLogger()
        self._propagation = propagation_engine
        self._hallucination_config = hallucination_config or HallucinationConfig()

    async def ingest(
        self,
        content: FactContent,
        source_agent_id: str,
        source_type: SourceType,
        tags: list[str] | None = None,
        derived_from: list[UUID] | None = None,
        metadata: dict[str, object] | None = None,
        initial_confidence: float | None = None,
    ) -> IngestResult:
        """Run the full ingest pipeline.

        Returns IngestResult with the stored fact and any detected conflicts/corroborations,
        or a rejection reason if the fact was not accepted.
        """
        t0 = time.monotonic()

        # 1. Input validation (FactContent validates itself in __post_init__)
        # Additional validation beyond what FactContent enforces
        agent = await self._agent_repo.get_by_id(source_agent_id)
        if agent is None:
            await self._ops.log(
                "ingest", "rejected",
                agent_id=source_agent_id,
                latency_ms=ms_since(t0),
                detail={"reason": "unknown_agent"},
            )
            return IngestResult(
                rejection=RejectionReason(
                    code="unknown_agent",
                    message=f"Agent '{source_agent_id}' not registered. Call connect() first.",
                )
            )

        # 2. Hallucination detection (lightweight, no LLM)
        hallucination_result = check_hallucination(
            content=content,
            source_type=source_type,
            agent=agent,
            claimed_confidence=initial_confidence,
            config=self._hallucination_config,
        )
        if hallucination_result.flagged:
            if hallucination_result.total_penalty >= self._hallucination_config.reject_threshold:
                rejection = hallucination_result.to_rejection()
                await self._ops.log(
                    "ingest", "rejected",
                    agent_id=source_agent_id,
                    latency_ms=ms_since(t0),
                    detail={
                        "reason": "hallucination",
                        "flags": [f.code for f in hallucination_result.flags],
                        "total_penalty": hallucination_result.total_penalty,
                    },
                )
                return IngestResult(rejection=rejection)

            # Below reject threshold: reduce confidence instead of blocking
            penalty = hallucination_result.total_penalty
            if initial_confidence is not None:
                initial_confidence = max(0.0, initial_confidence - penalty)
            else:
                # Penalty applied after confidence is computed from source_type
                metadata = dict(metadata or {})
                metadata["hallucination_penalty"] = penalty

        # 3. Predicate resolution
        predicate_canonical = resolve_predicate(content.predicate)

        # 4. Embedding generation
        embedding_text = content.to_embedding_text()
        embedding = await self._embedding.embed(embedding_text)

        # 5. Pre-commit contradiction check
        # Find existing facts with same subject
        candidates = await self._fact_repo.find_by_subject(
            content.subject, active_only=True
        )

        # Run conflict detection
        # Build a temporary fact for comparison (not yet stored)
        now = datetime.now()
        fact_id = uuid4()
        fact_metadata: dict[str, object] = dict(metadata or {})
        vectors = [normalize_vector(fact_metadata.get("version_vector"))]
        vectors.extend(
            normalize_vector(candidate.metadata.get("version_vector"))
            for candidate in candidates
        )
        merged_vector = merge_vectors(vectors)
        fact_metadata["version_vector"] = next_vector(merged_vector, source_agent_id)
        fact_metadata.setdefault("origin_agent_id", source_agent_id)
        fact_metadata.setdefault("causal_timestamp", now.isoformat())

        temp_fact = Fact(
            id=fact_id,
            content=content,
            source_agent_id=source_agent_id,
            source_type=source_type,
            confidence=0.0,  # placeholder
            trust_score=0.0,  # placeholder
            valid_from=now,
            created_at=now,
            predicate_canonical=predicate_canonical,
            embedding=embedding,
            tags=tags or [],
            derived_from=derived_from or [],
            metadata=fact_metadata,
        )

        conflict_results = detect_conflicts(
            temp_fact,
            candidates,
            contradiction_threshold=self._config.contradiction_threshold,
            corroboration_threshold=self._config.corroboration_threshold,
        )

        contradictions = [
            c for c in conflict_results if c.result == DetectionResult.CONTRADICTION
        ]
        corroborations = [
            c for c in conflict_results if c.result == DetectionResult.CORROBORATION
        ]

        # 6. Trust & confidence scoring
        confidence = (
            initial_confidence
            if initial_confidence is not None
            else compute_initial_confidence(source_type)
        )

        # Apply hallucination penalty to computed confidence
        hallucination_penalty = fact_metadata.get("hallucination_penalty")
        if isinstance(hallucination_penalty, float) and hallucination_penalty > 0:
            confidence = max(0.0, confidence - hallucination_penalty)
        trust_score = compute_trust_score(
            source_type,
            agent=agent,
            corroboration_count=len(corroborations),
        )

        # 7. Build the final fact
        fact = Fact(
            id=fact_id,
            content=content,
            source_agent_id=source_agent_id,
            source_type=source_type,
            confidence=confidence,
            trust_score=trust_score,
            valid_from=now,
            created_at=now,
            predicate_canonical=predicate_canonical,
            embedding=embedding,
            tags=tags or [],
            derived_from=derived_from or [],
            metadata=fact_metadata,
            corroboration_count=len(corroborations),
        )

        # 8. Store
        stored_fact = await self._fact_repo.insert(fact)

        # 9. Record conflict/corroboration relations
        for contradiction in contradictions:
            # Create conflict record
            conflict = Conflict(
                id=uuid4(),
                fact_a_id=contradiction.existing_fact.id,
                fact_b_id=stored_fact.id,
                status=ConflictStatus.DETECTED,
            )
            await self._conflict_repo.insert(conflict)

            # Create relation
            relation = FactRelation(
                id=uuid4(),
                source_fact_id=stored_fact.id,
                target_fact_id=contradiction.existing_fact.id,
                relation_type=RelationType.CONTRADICTS,
                created_by="system:ingest",
            )
            await self._relation_repo.insert(relation)

            # Flag both facts
            await self._fact_repo.update_conflict_status(
                stored_fact.id, "unresolved"
            )
            await self._fact_repo.update_conflict_status(
                contradiction.existing_fact.id, "unresolved"
            )

        for corroboration in corroborations:
            # Create relation
            relation = FactRelation(
                id=uuid4(),
                source_fact_id=stored_fact.id,
                target_fact_id=corroboration.existing_fact.id,
                relation_type=RelationType.CORROBORATES,
                created_by="system:ingest",
            )
            await self._relation_repo.insert(relation)

            # Bump corroboration count on existing fact
            existing = corroboration.existing_fact
            await self._fact_repo.update_scores(
                existing.id,
                corroboration_count=existing.corroboration_count + 1,
            )

        # 10. Update agent stats
        await self._agent_repo.update_trust_stats(
            source_agent_id,
            facts_contributed_delta=1,
            facts_contradicted_delta=len(contradictions),
        )

        # 11. Handle derived_from relations
        if derived_from:
            for parent_id in derived_from:
                relation = FactRelation(
                    id=uuid4(),
                    source_fact_id=stored_fact.id,
                    target_fact_id=parent_id,
                    relation_type=RelationType.DERIVED_FROM,
                    created_by="system:ingest",
                )
                await self._relation_repo.insert(relation)

        # 12. Propagation — evaluate subscriptions and push to matching agents
        if self._propagation is not None:
            await self._propagation.propagate(stored_fact, source_agent_id)

        await self._ops.log(
            "ingest", "success",
            agent_id=source_agent_id,
            fact_id=stored_fact.id,
            latency_ms=ms_since(t0),
            detail={
                "subject": content.subject,
                "predicate": predicate_canonical or content.predicate,
                "confidence": confidence,
                "trust_score": trust_score,
                "contradictions": len(contradictions),
                "corroborations": len(corroborations),
            },
        )

        return IngestResult(
            fact=stored_fact,
            conflicts=contradictions,
            corroborations=corroborations,
        )
