"""Unit tests for legacy migration importers."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from mycelium.client.client import MyceliumClient
from mycelium.config import MyceliumConfig
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.migration.base import (
    MIGRATION_CONFIDENCE,
    MigrationRecord,
    MigrationResult,
    MigrationSource,
)
from mycelium.migration.lancedb_importer import extract_from_lancedb, import_lancedb_memories
from mycelium.migration.memory_file_extractor import (
    MemoryExtractionError,
    OpenAIMemoryFactExtractor,
    extract_from_memory_file,
    extract_from_memory_files,
    import_memory_file_records,
)
from mycelium.migration.runner import FullMigrationResult
from mycelium.migration.supabase_importer import import_supabase_learnings
from mycelium.ops.logger import InMemoryOpsLogger
from mycelium.storage.memory import (
    InMemoryAgentRepository,
    InMemoryConflictRepository,
    InMemoryEventLog,
    InMemoryFactRepository,
    InMemoryRelationRepository,
    InMemorySubscriptionRepository,
)
from mycelium.transport.in_process import InProcessTransport


@pytest.fixture
async def client() -> MyceliumClient:
    """Create a connected MyceliumClient with in-memory storage."""
    c = MyceliumClient(
        agent_id="migration-agent",
        role="migration",
        config=MyceliumConfig(),
        fact_repo=InMemoryFactRepository(),
        agent_repo=InMemoryAgentRepository(),
        conflict_repo=InMemoryConflictRepository(),
        relation_repo=InMemoryRelationRepository(),
        subscription_repo=InMemorySubscriptionRepository(),
        event_log=InMemoryEventLog(),
        embedding_provider=MockEmbeddingProvider(dimension=64),
        transport=InProcessTransport(),
    )
    await c.connect()
    return c


class TestMigrationBase:
    def test_migration_record(self) -> None:
        record = MigrationRecord(
            subject="api-orders",
            predicate="has_status",
            object="healthy",
            tags=["api"],
        )
        assert record.subject == "api-orders"
        assert record.source_agent_id is None

    def test_migration_result_success_rate(self) -> None:
        result = MigrationResult(
            source=MigrationSource.SUPABASE,
            total_records=10,
            ingested=8,
            skipped=1,
            failed=1,
        )
        assert result.success_rate == 0.8

    def test_migration_result_empty(self) -> None:
        result = MigrationResult(source=MigrationSource.LANCEDB)
        assert result.success_rate == 1.0

    def test_migration_confidence(self) -> None:
        assert MIGRATION_CONFIDENCE == 0.7

    def test_migration_source_enum(self) -> None:
        assert MigrationSource.SUPABASE.value == "supabase"
        assert MigrationSource.LANCEDB.value == "lancedb"
        assert MigrationSource.MEMORY_FILE.value == "memory_file"


class TestSupabaseImporter:
    @pytest.mark.asyncio
    async def test_import_records(self, client: MyceliumClient) -> None:
        records = [
            MigrationRecord(
                subject="api-orders",
                predicate="has_status",
                object="healthy",
                tags=["api"],
                original_id="sb-1",
            ),
            MigrationRecord(
                subject="database-main",
                predicate="has_status",
                object="running",
                tags=["infra"],
                original_id="sb-2",
            ),
        ]

        result = await import_supabase_learnings(records, client)

        assert result.source == MigrationSource.SUPABASE
        assert result.total_records == 2
        assert result.ingested == 2
        assert result.failed == 0
        assert result.finished_at is not None

    @pytest.mark.asyncio
    async def test_import_empty(self, client: MyceliumClient) -> None:
        result = await import_supabase_learnings([], client)
        assert result.total_records == 0
        assert result.ingested == 0
        assert result.success_rate == 1.0

    @pytest.mark.asyncio
    async def test_import_logs_to_ops(self, client: MyceliumClient) -> None:
        ops = InMemoryOpsLogger()
        records = [
            MigrationRecord(
                subject="test",
                predicate="is",
                object="value",
                original_id="sb-1",
            ),
        ]

        await import_supabase_learnings(records, client, ops_logger=ops)

        entries = await ops.find_by_operation("migration")
        assert len(entries) == 1
        assert entries[0].detail is not None
        assert entries[0].detail["source"] == "supabase"

    @pytest.mark.asyncio
    async def test_import_carries_migration_metadata(self, client: MyceliumClient) -> None:
        records = [
            MigrationRecord(
                subject="test-fact",
                predicate="has_value",
                object="42",
                original_id="sb-99",
            ),
        ]

        result = await import_supabase_learnings(records, client)
        assert result.ingested == 1

        # Verify the fact was ingested with migration metadata
        results = await client.query("test-fact has_value 42")
        assert len(results) >= 1
        matching = [
            item.fact
            for item in results
            if item.fact.content.subject == "test-fact"
        ]
        assert len(matching) >= 1
        assert matching[0].metadata.get("migrated_from") == "supabase"
        assert matching[0].metadata.get("original_id") == "sb-99"


class TestLanceDBImporter:
    def test_extract_basic(self) -> None:
        raw = [
            {"text": "API orders are healthy", "metadata": {"subject": "api-orders"}},
            {"content": "Database is running", "metadata": {"subject": "db-main"}},
        ]

        records = extract_from_lancedb(raw)
        assert len(records) == 2
        assert records[0].subject == "api-orders"
        assert records[0].object == "API orders are healthy"
        assert records[1].subject == "db-main"

    def test_extract_skips_empty(self) -> None:
        raw = [
            {"text": ""},
            {"text": "valid content", "metadata": {"subject": "test"}},
        ]
        records = extract_from_lancedb(raw)
        assert len(records) == 1

    def test_extract_fallback_fields(self) -> None:
        raw = [{"memory": "some memory", "id": "lance-42"}]
        records = extract_from_lancedb(raw)
        assert len(records) == 1
        assert records[0].object == "some memory"
        assert records[0].subject == "unknown"
        assert records[0].predicate == "has_memory"
        assert records[0].original_id == "lance-42"

    def test_extract_with_tags(self) -> None:
        raw = [
            {
                "text": "content",
                "metadata": {"subject": "test", "tags": ["a", "b"]},
            }
        ]
        records = extract_from_lancedb(raw)
        assert records[0].tags == ["a", "b"]

    def test_extract_string_tags_wrapped(self) -> None:
        raw = [
            {
                "text": "content",
                "metadata": {"subject": "test", "tags": "single-tag"},
            }
        ]
        records = extract_from_lancedb(raw)
        assert records[0].tags == ["single-tag"]

    @pytest.mark.asyncio
    async def test_import_records(self, client: MyceliumClient) -> None:
        records = [
            MigrationRecord(
                subject="memory-1",
                predicate="has_memory",
                object="API orders are healthy",
                tags=["api"],
                original_id="lance-1",
            ),
        ]

        result = await import_lancedb_memories(records, client)
        assert result.source == MigrationSource.LANCEDB
        assert result.ingested == 1
        assert result.failed == 0

    @pytest.mark.asyncio
    async def test_import_with_ops_logger(self, client: MyceliumClient) -> None:
        ops = InMemoryOpsLogger()
        records = [
            MigrationRecord(
                subject="test",
                predicate="is",
                object="value",
                original_id="lance-1",
            ),
        ]

        await import_lancedb_memories(records, client, ops_logger=ops)

        entries = await ops.find_by_operation("migration")
        assert len(entries) == 1
        assert entries[0].detail is not None
        assert entries[0].detail["source"] == "lancedb"


class TestFullMigrationResult:
    def test_aggregate_counts(self) -> None:
        r1 = MigrationResult(
            source=MigrationSource.SUPABASE,
            total_records=10,
            ingested=8,
            skipped=1,
            failed=1,
            errors=["err1"],
        )
        r2 = MigrationResult(
            source=MigrationSource.LANCEDB,
            total_records=5,
            ingested=4,
            skipped=0,
            failed=1,
            errors=["err2"],
        )

        full = FullMigrationResult(results=[r1, r2])
        assert full.total_records == 15
        assert full.total_ingested == 12
        assert full.total_skipped == 1
        assert full.total_failed == 2
        assert full.all_errors == ["err1", "err2"]

    def test_empty(self) -> None:
        full = FullMigrationResult()
        assert full.total_records == 0
        assert full.total_ingested == 0
        assert full.all_errors == []


class _StubExtractor:
    def __init__(self, returned: list[MigrationRecord]) -> None:
        self.returned = returned
        self.calls: list[tuple[str, str, str]] = []

    async def extract(
        self,
        content: str,
        *,
        file_path: str,
        default_agent_id: str = "migration-agent",
    ) -> list[MigrationRecord]:
        self.calls.append((content, file_path, default_agent_id))
        return list(self.returned)


class TestMemoryFileExtractor:
    @pytest.mark.asyncio
    async def test_extract_from_memory_file_reads_and_delegates(self, tmp_path: Any) -> None:
        memory_file = tmp_path / "MEMORY.md"
        memory_file.write_text("# Memory\n- API moved to /v3", encoding="utf-8")

        expected = [
            MigrationRecord(
                subject="api-orders",
                predicate="moved_to",
                object="/v3/orders",
                original_id="MEMORY.md:0",
            )
        ]
        extractor = _StubExtractor(expected)

        records = await extract_from_memory_file(memory_file, extractor)
        assert records == expected
        assert len(extractor.calls) == 1
        content, file_path, default_agent = extractor.calls[0]
        assert "API moved to /v3" in content
        assert file_path.endswith("MEMORY.md")
        assert default_agent == "migration-agent"

    @pytest.mark.asyncio
    async def test_extract_from_memory_files_combines_records(self, tmp_path: Any) -> None:
        first = tmp_path / "MEMORY_A.md"
        second = tmp_path / "MEMORY_B.md"
        first.write_text("A", encoding="utf-8")
        second.write_text("B", encoding="utf-8")

        extractor = _StubExtractor(
            [
                MigrationRecord(
                    subject="x",
                    predicate="is",
                    object="a",
                    original_id="x",
                )
            ]
        )
        records = await extract_from_memory_files([first, second], extractor)
        assert len(records) == 2
        assert len(extractor.calls) == 2

    @pytest.mark.asyncio
    async def test_import_memory_file_records(self, client: MyceliumClient) -> None:
        records = [
            MigrationRecord(
                subject="jasper-code",
                predicate="prefers",
                object="explicit tests",
                tags=["workflow"],
                original_id="MEMORY.md:0",
            ),
            MigrationRecord(
                subject="jasper-trader",
                predicate="has_limit",
                object="max 3 concurrent positions",
                tags=["trading"],
                original_id="SOUL.md:1",
            ),
        ]

        result = await import_memory_file_records(records, client)
        assert result.source == MigrationSource.MEMORY_FILE
        assert result.total_records == 2
        assert result.ingested == 2
        assert result.failed == 0

        queried = await client.query("jasper-code prefers explicit tests")
        assert len(queried) >= 1
        matching = [item.fact for item in queried if item.fact.content.subject == "jasper-code"]
        assert matching[0].metadata.get("migrated_from") == "memory_file"
        assert matching[0].metadata.get("original_id") == "MEMORY.md:0"


class TestOpenAIMemoryFactExtractor:
    def test_requires_api_key(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="API key required"),
        ):
            OpenAIMemoryFactExtractor(api_key=None)

    @pytest.mark.asyncio
    async def test_extract_parses_json_response(self) -> None:
        extractor = OpenAIMemoryFactExtractor(api_key="sk-test")
        response = httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"facts":[{"subject":"api-orders",'
                                '"predicate":"moved_to","object":"/v3/orders",'
                                '"tags":["api"],"source_agent_id":"jasper-code"}]}'
                            )
                        }
                    }
                ]
            },
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)
        extractor.http_client = mock_client

        records = await extractor.extract(
            "# memory",
            file_path="/tmp/MEMORY.md",
            default_agent_id="migration-agent",
        )
        assert len(records) == 1
        assert records[0].subject == "api-orders"
        assert records[0].predicate == "moved_to"
        assert records[0].object == "/v3/orders"
        assert records[0].tags == ["api"]
        assert records[0].source_agent_id == "jasper-code"

    @pytest.mark.asyncio
    async def test_extract_non_200_raises(self) -> None:
        extractor = OpenAIMemoryFactExtractor(api_key="sk-test")
        response = httpx.Response(500, json={"error": {"message": "boom"}})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)
        extractor.http_client = mock_client

        with pytest.raises(MemoryExtractionError, match="OpenAI API error 500"):
            await extractor.extract(
                "# memory",
                file_path="/tmp/MEMORY.md",
            )

    @pytest.mark.asyncio
    async def test_extract_invalid_json_raises(self) -> None:
        extractor = OpenAIMemoryFactExtractor(api_key="sk-test")
        response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not-json"}}]},
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)
        extractor.http_client = mock_client

        with pytest.raises(MemoryExtractionError, match="Invalid extractor JSON"):
            await extractor.extract("# memory", file_path="/tmp/MEMORY.md")
