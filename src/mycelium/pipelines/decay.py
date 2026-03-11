"""DecayCycleRunner — periodic decay and garbage collection (Phase 3).

Scans active facts and expires those that meet decay criteria:
- Confidence below threshold (low-quality facts)
- Verification failed and not corrected
- Stale: not accessed or verified within a configurable window

Follows the same start/stop lifecycle pattern as ContradictionSweeper.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING

from mycelium.domain.types import Fact, VerificationStatus
from mycelium.ops.logger import NullOpsLogger, OpsLogger

if TYPE_CHECKING:
    from mycelium.storage.protocols import FactRepository

logger = logging.getLogger(__name__)


@dataclass
class DecayConfig:
    """Tuning knobs for the decay cycle."""

    min_confidence: float = 0.15
    """Facts below this confidence are expired."""

    stale_days: int = 90
    """Facts not accessed or verified in this many days are marked stale."""

    expire_failed_verification: bool = True
    """Expire facts whose verification_status is FAILED."""

    cycle_interval_hours: int = 24
    """How often the background loop runs."""

    trading_ttl_hours: int = 4
    """TTL for trading/market facts."""

    service_status_ttl_hours: int = 24
    """TTL for service health/status facts."""


@dataclass
class DecayCycleResult:
    """Result of a single decay cycle."""

    facts_scanned: int = 0
    expired_low_confidence: int = 0
    expired_failed_verification: int = 0
    expired_ttl: int = 0
    marked_stale: int = 0


class _Action(Enum):
    """Internal enum for decay actions."""

    NONE = "none"
    EXPIRE_LOW_CONFIDENCE = "expire_low_confidence"
    EXPIRE_FAILED_VERIFICATION = "expire_failed_verification"
    EXPIRE_TTL = "expire_ttl"
    MARK_STALE = "mark_stale"


class DecayCycleRunner:
    """Background runner that periodically decays and garbage-collects facts.

    Usage:
        runner = DecayCycleRunner(fact_repo, config=DecayConfig())
        result = await runner.run_cycle()          # single cycle
        await runner.start()                       # background loop
        await runner.stop()
    """

    def __init__(
        self,
        fact_repo: FactRepository,
        config: DecayConfig | None = None,
        ops_logger: OpsLogger | None = None,
    ) -> None:
        self._fact_repo = fact_repo
        self._config = config or DecayConfig()
        self._ops = ops_logger or NullOpsLogger()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def run_cycle(self, now: datetime | None = None) -> DecayCycleResult:
        """Run a single decay cycle against all active facts."""
        now = now or datetime.now(UTC)
        stale_cutoff = now - timedelta(days=self._config.stale_days)

        active_facts = await self._fact_repo.find_all_active()
        result = DecayCycleResult(facts_scanned=len(active_facts))

        for fact in active_facts:
            action = self._evaluate(fact, stale_cutoff, now)

            if action == _Action.EXPIRE_LOW_CONFIDENCE:
                await self._fact_repo.expire(fact.id, expired_at=now)
                result.expired_low_confidence += 1

            elif action == _Action.EXPIRE_FAILED_VERIFICATION:
                await self._fact_repo.expire(fact.id, expired_at=now)
                result.expired_failed_verification += 1

            elif action == _Action.EXPIRE_TTL:
                await self._fact_repo.expire(fact.id, expired_at=now)
                result.expired_ttl += 1

            elif action == _Action.MARK_STALE:
                await self._fact_repo.update_verification(
                    fact.id, status=VerificationStatus.STALE, verified_at=now
                )
                result.marked_stale += 1

        await self._ops.log(
            "decay_cycle",
            "complete",
            detail={
                "facts_scanned": result.facts_scanned,
                "expired_low_confidence": result.expired_low_confidence,
                "expired_failed_verification": result.expired_failed_verification,
                "expired_ttl": result.expired_ttl,
                "marked_stale": result.marked_stale,
            },
        )

        logger.info(
            "Decay cycle: scanned=%d, expired_low=%d, expired_failed=%d, expired_ttl=%d, stale=%d",
            result.facts_scanned,
            result.expired_low_confidence,
            result.expired_failed_verification,
            result.expired_ttl,
            result.marked_stale,
        )

        return result

    def _evaluate(self, fact: Fact, stale_cutoff: datetime, now: datetime) -> _Action:
        """Determine what decay action (if any) applies to a fact."""
        # 1. Low confidence → expire
        if fact.confidence < self._config.min_confidence:
            return _Action.EXPIRE_LOW_CONFIDENCE

        # 2. Failed verification → expire
        if (
            self._config.expire_failed_verification
            and fact.verification_status == VerificationStatus.FAILED
        ):
            return _Action.EXPIRE_FAILED_VERIFICATION

        # 3. Type TTLs
        ttl_hours = self._infer_ttl_hours(fact)
        if ttl_hours is not None:
            ttl_cutoff = fact.valid_from + timedelta(hours=ttl_hours)
            if now >= ttl_cutoff:
                return _Action.EXPIRE_TTL

        # 4. Stale: never accessed/verified and created before cutoff,
        #    or last activity before cutoff
        last_activity = fact.last_accessed_at or fact.last_verified_at or fact.created_at
        if last_activity < stale_cutoff and fact.verification_status != VerificationStatus.STALE:
            return _Action.MARK_STALE

        return _Action.NONE

    def _infer_ttl_hours(self, fact: Fact) -> int | None:
        metadata_ttl = fact.metadata.get("ttl_hours")
        if isinstance(metadata_ttl, int) and metadata_ttl > 0:
            return metadata_ttl
        if isinstance(metadata_ttl, float) and metadata_ttl > 0:
            return int(metadata_ttl)

        tags = {tag.lower() for tag in fact.tags}
        subject = fact.content.subject.lower()
        predicate = (fact.predicate_canonical or fact.content.predicate).lower()

        if (
            any(tag.startswith("trading") for tag in tags)
            or any(token in tags for token in {"market", "signal", "position", "risk"})
            or any(token in subject for token in ("eurusd", "btc", "eth", "position", "pnl"))
        ):
            return self._config.trading_ttl_hours

        architecture_tagged = (
            any(tag.startswith("architecture") for tag in tags)
            or any(tag.startswith("design") for tag in tags)
            or any(token in subject for token in ("architecture", "adr", "design doc"))
        )
        if architecture_tagged:
            return None

        is_status_predicate = predicate == "has_status"
        service_like_subject = any(
            token in subject
            for token in ("api", "service", "broker", "adapter", "pipeline")
        )
        if is_status_predicate and service_like_subject:
            return self._config.service_status_ttl_hours

        return None

    async def start(self) -> None:
        """Start the background decay loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background decay loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_loop(self) -> None:
        """Internal loop — runs until stopped."""
        while self._running:
            try:
                await self.run_cycle()
            except Exception:
                logger.exception("Decay cycle failed")

            try:
                await asyncio.sleep(self._config.cycle_interval_hours * 3600)
            except asyncio.CancelledError:
                break
