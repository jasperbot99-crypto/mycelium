"""CLI entrypoint for migration workflows."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, cast

import asyncpg

from mycelium.client.client import MyceliumClient
from mycelium.config import MyceliumConfig
from mycelium.embeddings.openai import OpenAIEmbeddingProvider
from mycelium.migration.base import MigrationSource
from mycelium.migration.lancedb_importer import extract_from_lancedb, import_lancedb_memories
from mycelium.migration.memory_file_extractor import (
    OpenAIMemoryFactExtractor,
    extract_from_memory_files,
    import_memory_file_records,
)
from mycelium.migration.runner import FullMigrationResult, MigrationSourceRun, run_migration_plan
from mycelium.migration.supabase_importer import extract_from_supabase, import_supabase_learnings
from mycelium.storage.postgres.repositories import (
    PostgresAgentRepository,
    PostgresConflictRepository,
    PostgresEventLog,
    PostgresFactRepository,
    PostgresRelationRepository,
    PostgresSubscriptionRepository,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mycelium migration CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_schema = subparsers.add_parser(
        "apply-schema",
        help="Apply a SQL migration file to target Mycelium database.",
    )
    apply_schema.add_argument("--database-url", required=True)
    apply_schema.add_argument("--file", default="migrations/001_initial.sql")

    run = subparsers.add_parser(
        "run",
        help="Run migration import plan from one or more legacy sources.",
    )
    run.add_argument("--database-url", required=True)
    run.add_argument("--agent-id", default="migration-agent")
    run.add_argument(
        "--source",
        choices=["supabase", "lancedb", "memory_file", "all"],
        default="all",
    )
    run.add_argument("--supabase-database-url")
    run.add_argument(
        "--supabase-query",
        default=(
            "SELECT id, agent_id, subject, predicate, content, context, tags, created_at "
            "FROM shared_learnings ORDER BY created_at ASC"
        ),
    )
    run.add_argument("--lancedb-json")
    run.add_argument("--memory-file", action="append", default=[])
    run.add_argument("--openai-api-key")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--stop-on-error", action="store_true")

    return parser


async def _apply_schema(database_url: str, file_path: str) -> None:
    sql = Path(file_path).read_text(encoding="utf-8")
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _build_client(database_url: str, agent_id: str) -> tuple[MyceliumClient, asyncpg.Pool]:
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    embedding_provider = OpenAIEmbeddingProvider()
    client = MyceliumClient(
        agent_id=agent_id,
        role="migration",
        config=MyceliumConfig(),
        fact_repo=PostgresFactRepository(pool),
        agent_repo=PostgresAgentRepository(pool),
        conflict_repo=PostgresConflictRepository(pool),
        relation_repo=PostgresRelationRepository(pool),
        subscription_repo=PostgresSubscriptionRepository(pool),
        event_log=PostgresEventLog(pool),
        embedding_provider=embedding_provider,
    )
    await client.connect()
    return client, pool


def _load_json_records(path: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("LanceDB JSON must be a list of objects")
    rows: list[dict[str, Any]] = []
    for item in cast("list[object]", payload):
        if isinstance(item, dict):
            rows.append(cast("dict[str, Any]", item))
    return rows


async def _build_source_runs(args: argparse.Namespace) -> list[MigrationSourceRun]:
    source_runs: list[MigrationSourceRun] = []
    include_all = args.source == "all"

    if include_all or args.source == "supabase":
        if not args.supabase_database_url:
            raise ValueError("--supabase-database-url is required for supabase source")
        conn = await asyncpg.connect(args.supabase_database_url)
        try:
            supabase_records = await extract_from_supabase(
                cast("Any", conn),
                query=args.supabase_query,
            )
        finally:
            await conn.close()
        source_runs.append(
            MigrationSourceRun(
                source=MigrationSource.SUPABASE,
                records=supabase_records,
                importer=import_supabase_learnings,
            )
        )

    if include_all or args.source == "lancedb":
        if not args.lancedb_json:
            raise ValueError("--lancedb-json is required for lancedb source")
        lancedb_records = extract_from_lancedb(_load_json_records(args.lancedb_json))
        source_runs.append(
            MigrationSourceRun(
                source=MigrationSource.LANCEDB,
                records=lancedb_records,
                importer=import_lancedb_memories,
            )
        )

    if include_all or args.source == "memory_file":
        if not args.memory_file:
            raise ValueError("--memory-file is required for memory_file source")
        extractor = OpenAIMemoryFactExtractor(api_key=args.openai_api_key)
        try:
            memory_records = await extract_from_memory_files(args.memory_file, extractor)
        finally:
            await extractor.close()
        source_runs.append(
            MigrationSourceRun(
                source=MigrationSource.MEMORY_FILE,
                records=memory_records,
                importer=import_memory_file_records,
            )
        )

    return source_runs


def print_result(result: FullMigrationResult) -> None:
    print("Migration finished")
    print(
        f"Totals: records={result.total_records} ingested={result.total_ingested} "
        f"skipped={result.total_skipped} failed={result.total_failed}"
    )
    for source_result in result.results:
        print(
            f"- {source_result.source.value}: total={source_result.total_records} "
            f"ingested={source_result.ingested} skipped={source_result.skipped} "
            f"failed={source_result.failed}"
        )
    if result.all_errors:
        print("Errors:")
        for error in result.all_errors:
            print(f"  - {error}")


async def _run(args: argparse.Namespace) -> None:
    source_runs = await _build_source_runs(args)
    if args.dry_run:
        result = await run_migration_plan(
            client=None,
            source_runs=source_runs,
            dry_run=True,
        )
        print_result(result)
        return

    client, pool = await _build_client(args.database_url, args.agent_id)
    try:
        result = await run_migration_plan(
            client=client,
            source_runs=source_runs,
            dry_run=args.dry_run,
            stop_on_error=args.stop_on_error,
        )
        print_result(result)
    finally:
        await client.disconnect()
        await pool.close()


async def _main_async(args: argparse.Namespace) -> None:
    if args.command == "apply-schema":
        await _apply_schema(args.database_url, args.file)
        print(f"Applied schema file: {args.file}")
        return
    if args.command == "run":
        await _run(args)
        return
    raise ValueError(f"Unsupported command: {args.command}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(_main_async(args))
