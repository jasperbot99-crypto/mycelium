"""ContradictionSweeper — post-commit background sweep (D-ARCH-3, Phase B).

Catches contradictions missed by pre-commit check — specifically concurrent
writes where two contradictory facts are in-flight simultaneously.

Runs on a configurable interval, scans facts created since the last sweep,
and uses the same ConflictDetector logic as the pre-commit check.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from mycelium.domain.conflict import DetectionResult, detect_conflicts
from mycelium.domain.types import (
    Conflict,
    ConflictStatus,
    FactRelation,
    RelationType,
)

if TYPE_CHECKING:
    from mycelium.storage.protocols import (
        ConflictRepository,
        FactRepository,
        RelationRepository,
    )

logger = logging.getLogger(__name__)


class SweepResult:
    """Result of a single sweep cycle."""

    def __init__(
        self,
        facts_scanned: int = 0,
        contradictions_found: int = 0,
        corroborations_found: int = 0,
    ) -> None:
        self.facts_scanned = facts_scanned
        self.contradictions_found = contradictions_found
        self.corroborations_found = corroborations_found


class ContradictionSweeper:
    """Background sweep for contradictions missed by pre-commit check.

    Usage:
        sweeper = ContradictionSweeper(fact_repo, conflict_repo, relation_repo, ...)
        # Run a single sweep:
        result = await sweeper.sweep()
        # Run continuously in background:
        await sweeper.start()
        # Stop:
        await sweeper.stop()
    """

    def __init__(
        self,
        fact_repo: FactRepository,
        conflict_repo: ConflictRepository,
        relation_repo: RelationRepository,
        contradiction_threshold: float = 0.75,
        corroboration_threshold: float = 0.95,
        sweep_interval_s: int = 60,
    ) -> None:
        self._fact_repo = fact_repo
        self._conflict_repo = conflict_repo
        self._relation_repo = relation_repo
        self._contradiction_threshold = contradiction_threshold
        self._corroboration_threshold = corroboration_threshold
        self._sweep_interval_s = sweep_interval_s

        self._last_sweep_at: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def last_sweep_at(self) -> datetime | None:
        return self._last_sweep_at

    async def sweep(self) -> SweepResult:
        """Run a single sweep cycle.

        Scans facts created since the last sweep and checks each against
        all other active facts with the same subject.
        """
        since = self._last_sweep_at or datetime.min
        sweep_start = datetime.now()

        new_facts = await self._fact_repo.find_created_since(
            since, active_only=True
        )

        result = SweepResult(facts_scanned=len(new_facts))

        for new_fact in new_facts:
            if new_fact.embedding is None:
                continue

            # Get candidates: active facts with same subject (excluding self)
            candidates = await self._fact_repo.find_by_subject(
                new_fact.content.subject, active_only=True
            )
            candidates = [c for c in candidates if c.id != new_fact.id]

            conflict_results = detect_conflicts(
                new_fact,
                candidates,
                contradiction_threshold=self._contradiction_threshold,
                corroboration_threshold=self._corroboration_threshold,
            )

            for cr in conflict_results:
                # Check if this conflict pair already exists
                existing_conflicts = await self._conflict_repo.find_for_fact(
                    new_fact.id
                )
                already_recorded = any(
                    (c.fact_a_id == cr.existing_fact.id and c.fact_b_id == new_fact.id)
                    or (c.fact_a_id == new_fact.id and c.fact_b_id == cr.existing_fact.id)
                    for c in existing_conflicts
                )

                if already_recorded:
                    continue

                if cr.result == DetectionResult.CONTRADICTION:
                    result.contradictions_found += 1

                    conflict = Conflict(
                        id=uuid4(),
                        fact_a_id=cr.existing_fact.id,
                        fact_b_id=new_fact.id,
                        status=ConflictStatus.DETECTED,
                    )
                    await self._conflict_repo.insert(conflict)

                    relation = FactRelation(
                        id=uuid4(),
                        source_fact_id=new_fact.id,
                        target_fact_id=cr.existing_fact.id,
                        relation_type=RelationType.CONTRADICTS,
                        created_by="system:sweeper",
                    )
                    await self._relation_repo.insert(relation)

                    await self._fact_repo.update_conflict_status(
                        new_fact.id, "unresolved"
                    )
                    await self._fact_repo.update_conflict_status(
                        cr.existing_fact.id, "unresolved"
                    )

                    logger.info(
                        "Sweeper: contradiction detected between %s and %s",
                        new_fact.id,
                        cr.existing_fact.id,
                    )

                elif cr.result == DetectionResult.CORROBORATION:
                    result.corroborations_found += 1

                    # Check if corroboration relation already exists
                    existing_relations = await self._relation_repo.find_for_fact(
                        new_fact.id
                    )
                    already_related = any(
                        r.relation_type == RelationType.CORROBORATES
                        and (
                            (r.source_fact_id == new_fact.id
                             and r.target_fact_id == cr.existing_fact.id)
                            or (r.source_fact_id == cr.existing_fact.id
                                and r.target_fact_id == new_fact.id)
                        )
                        for r in existing_relations
                    )

                    if not already_related:
                        relation = FactRelation(
                            id=uuid4(),
                            source_fact_id=new_fact.id,
                            target_fact_id=cr.existing_fact.id,
                            relation_type=RelationType.CORROBORATES,
                            created_by="system:sweeper",
                        )
                        await self._relation_repo.insert(relation)

                        await self._fact_repo.update_scores(
                            cr.existing_fact.id,
                            corroboration_count=cr.existing_fact.corroboration_count + 1,
                        )

        self._last_sweep_at = sweep_start

        logger.info(
            "Sweep complete: scanned=%d, contradictions=%d, corroborations=%d",
            result.facts_scanned,
            result.contradictions_found,
            result.corroborations_found,
        )

        return result

    async def start(self) -> None:
        """Start the background sweep loop."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background sweep loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_loop(self) -> None:
        """Internal sweep loop — runs until stopped."""
        while self._running:
            try:
                await self.sweep()
            except Exception:
                logger.exception("Sweep cycle failed")

            try:
                await asyncio.sleep(self._sweep_interval_s)
            except asyncio.CancelledError:
                break
