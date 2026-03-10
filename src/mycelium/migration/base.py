"""Base migration types — SPEC Section 7.8, ARCHITECTURE.md Section 3.7.

All migrated facts:
- Start at confidence 0.7 (not yet verified through Mycelium)
- Carry migrated_from + migration_date metadata for traceability
- Can decay naturally if stale
- Migration is a cutover, not a gradual sync
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


def _string_list() -> list[str]:
    return []


def _tag_list() -> list[str]:
    return []


class MigrationSource(StrEnum):
    """Source systems for legacy migration."""

    SUPABASE = "supabase"
    LANCEDB = "lancedb"
    MEMORY_FILE = "memory_file"


@dataclass
class MigrationRecord:
    """A single item extracted from a legacy source, ready for ingest."""

    subject: str
    predicate: str
    object: str
    context: str | None = None
    tags: list[str] = field(default_factory=_tag_list)
    source_agent_id: str | None = None
    original_id: str | None = None
    original_created_at: datetime | None = None


@dataclass
class MigrationResult:
    """Result of running a migration."""

    source: MigrationSource
    total_records: int = 0
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=_string_list)
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None

    @property
    def success_rate(self) -> float:
        if self.total_records == 0:
            return 1.0
        return self.ingested / self.total_records


# Default confidence for migrated facts (spec 7.8)
MIGRATION_CONFIDENCE = 0.7
