"""Unit tests for migration CLI helpers."""

from __future__ import annotations

from typing import Any

from mycelium.migration.base import MigrationResult, MigrationSource
from mycelium.migration.cli import build_parser, print_result
from mycelium.migration.runner import FullMigrationResult


def test_parser_supports_run_command() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "--database-url",
            "postgresql://localhost:5432/mycelium_dev",
            "--source",
            "lancedb",
            "--lancedb-json",
            "/tmp/lancedb.json",
            "--dry-run",
        ]
    )

    assert args.command == "run"
    assert args.source == "lancedb"
    assert args.dry_run is True


def test_print_result_outputs_totals_and_sources(capsys: Any) -> None:
    result = FullMigrationResult(
        results=[
            MigrationResult(
                source=MigrationSource.SUPABASE,
                total_records=2,
                ingested=2,
                failed=0,
            ),
            MigrationResult(
                source=MigrationSource.LANCEDB,
                total_records=1,
                ingested=0,
                failed=1,
                errors=["bad row"],
            ),
        ]
    )

    print_result(result)
    out = capsys.readouterr().out
    assert "Totals: records=3 ingested=2 skipped=0 failed=1" in out
    assert "- supabase: total=2 ingested=2 skipped=0 failed=0" in out
    assert "- lancedb: total=1 ingested=0 skipped=0 failed=1" in out
    assert "bad row" in out
