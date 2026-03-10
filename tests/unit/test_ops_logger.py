"""Unit tests for ops logger — Protocol, InMemoryOpsLogger, NullOpsLogger."""

from __future__ import annotations

from uuid import uuid4

import pytest

from mycelium.ops.logger import InMemoryOpsLogger, NullOpsLogger, OpsLogEntry, OpsLogger


class TestOpsLogEntry:
    def test_create_entry(self) -> None:
        entry = OpsLogEntry(
            id=uuid4(),
            operation="ingest",
            status="success",
            agent_id="agent-a",
        )
        assert entry.operation == "ingest"
        assert entry.status == "success"
        assert entry.agent_id == "agent-a"
        assert entry.latency_ms is None
        assert entry.detail is None

    def test_entry_with_detail(self) -> None:
        fact_id = uuid4()
        entry = OpsLogEntry(
            id=uuid4(),
            operation="query",
            status="success",
            fact_id=fact_id,
            latency_ms=42,
            detail={"results": 5},
        )
        assert entry.fact_id == fact_id
        assert entry.latency_ms == 42
        assert entry.detail == {"results": 5}


class TestInMemoryOpsLogger:
    @pytest.fixture
    def logger(self) -> InMemoryOpsLogger:
        return InMemoryOpsLogger()

    @pytest.mark.asyncio
    async def test_log_appends_entry(self, logger: InMemoryOpsLogger) -> None:
        entry = await logger.log("ingest", "success", agent_id="agent-a")
        assert entry.operation == "ingest"
        assert entry.status == "success"
        assert len(logger.entries) == 1

    @pytest.mark.asyncio
    async def test_log_multiple(self, logger: InMemoryOpsLogger) -> None:
        await logger.log("ingest", "success", agent_id="agent-a")
        await logger.log("query", "success", agent_id="agent-b")
        await logger.log("ingest", "rejected", agent_id="agent-a")
        assert len(logger.entries) == 3

    @pytest.mark.asyncio
    async def test_find_by_operation(self, logger: InMemoryOpsLogger) -> None:
        await logger.log("ingest", "success")
        await logger.log("query", "success")
        await logger.log("ingest", "rejected")

        ingest_entries = await logger.find_by_operation("ingest")
        assert len(ingest_entries) == 2

        query_entries = await logger.find_by_operation("query")
        assert len(query_entries) == 1

    @pytest.mark.asyncio
    async def test_find_by_agent(self, logger: InMemoryOpsLogger) -> None:
        await logger.log("ingest", "success", agent_id="agent-a")
        await logger.log("ingest", "success", agent_id="agent-b")
        await logger.log("query", "success", agent_id="agent-a")

        a_entries = await logger.find_by_agent("agent-a")
        assert len(a_entries) == 2

        b_entries = await logger.find_by_agent("agent-b")
        assert len(b_entries) == 1

    @pytest.mark.asyncio
    async def test_find_by_operation_with_limit(self, logger: InMemoryOpsLogger) -> None:
        for _ in range(10):
            await logger.log("ingest", "success")

        results = await logger.find_by_operation("ingest", limit=5)
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_log_with_latency_and_detail(self, logger: InMemoryOpsLogger) -> None:
        fact_id = uuid4()
        entry = await logger.log(
            "ingest", "success",
            agent_id="agent-a",
            fact_id=fact_id,
            latency_ms=15,
            detail={"subject": "api-orders", "confidence": 0.8},
        )
        assert entry.fact_id == fact_id
        assert entry.latency_ms == 15
        assert entry.detail is not None
        assert entry.detail["subject"] == "api-orders"

    @pytest.mark.asyncio
    async def test_clear(self, logger: InMemoryOpsLogger) -> None:
        await logger.log("ingest", "success")
        await logger.log("query", "success")
        assert len(logger.entries) == 2

        logger.clear()
        assert len(logger.entries) == 0

    @pytest.mark.asyncio
    async def test_entries_returns_copy(self, logger: InMemoryOpsLogger) -> None:
        await logger.log("ingest", "success")
        entries = logger.entries
        entries.clear()
        assert len(logger.entries) == 1  # original unaffected

    @pytest.mark.asyncio
    async def test_protocol_conformance(self) -> None:
        logger = InMemoryOpsLogger()
        assert isinstance(logger, OpsLogger)


class TestNullOpsLogger:
    @pytest.mark.asyncio
    async def test_log_returns_entry(self) -> None:
        logger = NullOpsLogger()
        entry = await logger.log("ingest", "success", agent_id="agent-a")
        assert entry.operation == "ingest"

    @pytest.mark.asyncio
    async def test_find_returns_empty(self) -> None:
        logger = NullOpsLogger()
        assert await logger.find_by_operation("ingest") == []
        assert await logger.find_by_agent("agent-a") == []

    @pytest.mark.asyncio
    async def test_protocol_conformance(self) -> None:
        logger = NullOpsLogger()
        assert isinstance(logger, OpsLogger)
