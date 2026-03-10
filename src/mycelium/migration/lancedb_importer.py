"""LanceDB semantic memory importer — SPEC Section 7.8.

Reads from LanceDB tables and ingests as Mycelium facts.
Embeddings are recomputed (we're changing embedding model).
Old vectors are discarded — Mycelium generates its own via EmbeddingProvider.

Facts start at confidence 0.7 (reduced, not yet verified).
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from mycelium.domain.types import FactContent, SourceType
from mycelium.migration.base import (
    MIGRATION_CONFIDENCE,
    MigrationRecord,
    MigrationResult,
    MigrationSource,
)

if TYPE_CHECKING:
    from mycelium.client.client import MyceliumClient
    from mycelium.ops.logger import OpsLogger


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return {}


def _as_str(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if value is None:
        return default
    rendered = str(value).strip()
    return rendered if rendered else default


def _as_optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if value is None:
        return None
    return str(value)


def _as_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        parsed: list[str] = []
        for raw_tag in cast("list[object]", value):
            rendered = str(raw_tag).strip()
            if rendered:
                parsed.append(rendered)
        return parsed
    tag = str(value).strip()
    return [tag] if tag else []


def extract_from_lancedb(
    records: list[dict[str, Any]],
    agent_id: str = "migration-agent",
) -> list[MigrationRecord]:
    """Extract MigrationRecords from LanceDB query results.

    LanceDB records are expected to have at minimum:
    - text/content: the memory content
    - metadata (optional): dict with subject, tags, agent_id, etc.

    Since LanceDB schemas vary, this function handles common patterns
    and falls back to reasonable defaults.

    Args:
        records: Raw dicts from LanceDB table.to_pandas().to_dict('records') or similar.
        agent_id: Default agent_id for records without provenance.

    Returns:
        List of MigrationRecord ready for ingest.
    """
    migration_records: list[MigrationRecord] = []

    for i, row in enumerate(records):
        # Extract text content — try common field names
        text = (
            row.get("text")
            or row.get("content")
            or row.get("memory")
            or row.get("document")
            or ""
        )
        if not text:
            continue

        metadata = _as_dict(row.get("metadata", {}) or {})

        # Try to extract structured fields
        subject = _as_str(metadata.get("subject") or row.get("subject"), "unknown")
        predicate = _as_str(
            metadata.get("predicate") or row.get("predicate"), "has_memory"
        )
        tags = _as_tags(metadata.get("tags") or row.get("tags"))
        source_agent = _as_str(metadata.get("agent_id") or row.get("agent_id"), agent_id)
        original_id = str(row.get("id", f"lancedb-{i}"))

        created_at = None
        if "created_at" in row:
            with contextlib.suppress(ValueError, TypeError):
                created_at = datetime.fromisoformat(str(row["created_at"]))

        record = MigrationRecord(
            subject=subject,
            predicate=predicate,
            object=text,
            context=_as_optional_str(metadata.get("context")),
            tags=tags,
            source_agent_id=source_agent,
            original_id=original_id,
            original_created_at=created_at,
        )
        migration_records.append(record)

    return migration_records


async def import_lancedb_memories(
    records: list[MigrationRecord],
    client: MyceliumClient,
    ops_logger: OpsLogger | None = None,
) -> MigrationResult:
    """Ingest extracted LanceDB records into Mycelium.

    Embeddings are recomputed by the ingest pipeline —
    old LanceDB vectors are not used.

    Args:
        records: Pre-extracted migration records.
        client: Connected MyceliumClient to ingest through.
        ops_logger: Optional ops logger for migration tracking.

    Returns:
        MigrationResult with counts and any errors.
    """
    result = MigrationResult(
        source=MigrationSource.LANCEDB,
        total_records=len(records),
    )

    for record in records:
        try:
            content = FactContent(
                subject=record.subject,
                predicate=record.predicate,
                object=record.object,
                context=record.context,
            )

            ingest_result = await client.ingest(
                content=content,
                source_type=SourceType.AGENT_EXTRACTION,
                tags=record.tags,
                initial_confidence=MIGRATION_CONFIDENCE,
                metadata={
                    "migrated_from": MigrationSource.LANCEDB.value,
                    "migration_date": datetime.now().isoformat(),
                    "original_id": record.original_id,
                },
            )

            if ingest_result.accepted:
                result.ingested += 1
            else:
                result.skipped += 1
                reason = str(ingest_result.rejection) if ingest_result.rejection else "unknown"
                result.errors.append(
                    f"Record {record.original_id} skipped: {reason}"
                )

        except Exception as e:
            result.failed += 1
            result.errors.append(
                f"Record {record.original_id} failed: {e}"
            )

    result.finished_at = datetime.now()

    if ops_logger is not None:
        await ops_logger.log(
            "migration", "completed",
            detail={
                "source": MigrationSource.LANCEDB.value,
                "total": result.total_records,
                "ingested": result.ingested,
                "skipped": result.skipped,
                "failed": result.failed,
                "success_rate": result.success_rate,
            },
        )

    return result
