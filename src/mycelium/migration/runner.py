"""Migration runner — orchestrates full legacy migration.

SPEC Section 7.8: Migration is a cutover, not gradual sync.
When Mycelium is live, old sources are frozen, no dual-write.

Order:
1. Supabase shared_learnings (already structured, cleanest mapping)
2. LanceDB semantic memories (need re-embedding, but structured)
3. Memory files (requires LLM — may defer)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mycelium.migration.base import MigrationResult


def _result_list() -> list[MigrationResult]:
    return []


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
