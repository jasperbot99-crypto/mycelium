"""VerificationCycleRunner — periodic automated verification for unverified facts."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mycelium.domain.types import VerificationStatus
from mycelium.ops.logger import NullOpsLogger, OpsLogger

if TYPE_CHECKING:
    from mycelium.pipelines.verification import VerificationPipeline
    from mycelium.pipelines.verification_providers import (
        VerificationOutcome,
        VerificationProvider,
    )
    from mycelium.storage.protocols import FactRepository

logger = logging.getLogger(__name__)


@dataclass
class VerificationCycleConfig:
    """Tuning knobs for the verification cycle."""

    cycle_interval_hours: int = 24
    max_facts_per_cycle: int = 500


@dataclass
class VerificationCycleResult:
    """Result of one automated verification cycle."""

    facts_scanned: int = 0
    facts_verified: int = 0
    facts_stale: int = 0
    facts_failed: int = 0
    facts_skipped: int = 0


class VerificationCycleRunner:
    """Background verification runner using pluggable providers."""

    def __init__(
        self,
        fact_repo: FactRepository,
        verification_pipeline: VerificationPipeline,
        providers: list[VerificationProvider],
        config: VerificationCycleConfig | None = None,
        ops_logger: OpsLogger | None = None,
    ) -> None:
        self._fact_repo = fact_repo
        self._verification = verification_pipeline
        self._providers = providers
        self._config = config or VerificationCycleConfig()
        self._ops = ops_logger or NullOpsLogger()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def run_cycle(self, now: datetime | None = None) -> VerificationCycleResult:
        now = now or datetime.now(UTC)
        del now

        if not self._providers:
            return VerificationCycleResult()

        active_facts = await self._fact_repo.find_all_active()
        result = VerificationCycleResult()

        for fact in active_facts:
            if result.facts_scanned >= self._config.max_facts_per_cycle:
                break
            result.facts_scanned += 1

            if fact.verification_status != VerificationStatus.UNVERIFIED:
                result.facts_skipped += 1
                continue

            outcomes: list[VerificationOutcome] = []
            for provider in self._providers:
                outcomes.append(await provider.check(fact))

            chosen = _select_outcome(outcomes)
            await self._verification.verify(
                fact_id=fact.id,
                method=chosen.method,
                status=chosen.status,
                reason=chosen.reason,
            )
            if chosen.status == VerificationStatus.VERIFIED:
                result.facts_verified += 1
            elif chosen.status == VerificationStatus.STALE:
                result.facts_stale += 1
            elif chosen.status == VerificationStatus.FAILED:
                result.facts_failed += 1

        await self._ops.log(
            "verification_cycle",
            "complete",
            detail={
                "facts_scanned": result.facts_scanned,
                "facts_verified": result.facts_verified,
                "facts_stale": result.facts_stale,
                "facts_failed": result.facts_failed,
                "facts_skipped": result.facts_skipped,
            },
        )

        return result

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.run_cycle()
            except Exception:
                logger.exception("verification cycle failed")
            try:
                await asyncio.sleep(self._config.cycle_interval_hours * 3600)
            except asyncio.CancelledError:
                break


def _select_outcome(outcomes: list[VerificationOutcome]) -> VerificationOutcome:
    for outcome in outcomes:
        if outcome.status == VerificationStatus.FAILED:
            return outcome
    for outcome in outcomes:
        if outcome.status == VerificationStatus.STALE:
            return outcome
    return outcomes[0]
