"""Migration runner — orchestrates full legacy migration.

SPEC Section 7.8: Migration is a cutover, not gradual sync.
When Mycelium is live, old sources are frozen, no dual-write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from mycelium.migration.base import MigrationResult, MigrationSource

if TYPE_CHECKING:
    from mycelium.client.client import MyceliumClient
    from mycelium.migration.base import MigrationRecord
    from mycelium.ops.logger import OpsLogger


def _result_list() -> list[MigrationResult]:
    return []


class MigrationImporter(Protocol):
    """Callable importer for one migration source."""

    async def __call__(
        self,
        records: list[MigrationRecord],
        client: MyceliumClient,
        ops_logger: OpsLogger | None = None,
    ) -> MigrationResult: ...


@dataclass(frozen=True)
class MigrationSourceRun:
    """One source execution plan for ordered migration."""

    source: MigrationSource
    records: list[MigrationRecord]
    importer: MigrationImporter


@dataclass
class FullMigrationResult:
    """Result of running the complete migration pipeline."""

    results: list[MigrationResult] = field(default_factory=_result_list)

    @property
    def total_ingested(self) -> int:
        return sum(r.ingested for r in self.results)

    @property
    def total_failed(self) -> int:
        return sum(r.failed for r in self.results)

    @property
    def total_skipped(self) -> int:
        return sum(r.skipped for r in self.results)

    @property
    def total_records(self) -> int:
        return sum(r.total_records for r in self.results)

    @property
    def all_errors(self) -> list[str]:
        errors: list[str] = []
        for r in self.results:
            errors.extend(r.errors)
        return errors


async def run_migration_plan(
    client: MyceliumClient | None,
    source_runs: list[MigrationSourceRun],
    *,
    ops_logger: OpsLogger | None = None,
    dry_run: bool = False,
    stop_on_error: bool = False,
) -> FullMigrationResult:
    """Run an ordered migration plan.

    Args:
        client: Connected Mycelium client used for ingest.
        source_runs: Ordered source plans.
        ops_logger: Optional ops logger used by importers.
        dry_run: If true, computes totals but skips ingest.
        stop_on_error: If true, stop after the first source with failures.
    """
    if not dry_run and client is None:
        raise ValueError("client is required when dry_run is False")

    aggregated = FullMigrationResult()
    for source_run in source_runs:
        if dry_run:
            result = MigrationResult(
                source=source_run.source,
                total_records=len(source_run.records),
                skipped=len(source_run.records),
                started_at=datetime.now(),
                finished_at=datetime.now(),
            )
            aggregated.results.append(result)
            continue

        assert client is not None
        result = await source_run.importer(source_run.records, client, ops_logger=ops_logger)
        aggregated.results.append(result)

        if stop_on_error and (result.failed > 0 or result.errors):
            break

    return aggregated
