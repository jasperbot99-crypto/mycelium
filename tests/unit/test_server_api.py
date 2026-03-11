from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from mycelium.config import MyceliumConfig
from mycelium.domain.types import (
    Fact,
    FactContent,
    FeedbackResult,
    FeedbackSignal,
    SourceType,
    VerificationStatus,
)
from mycelium.pipelines.ingest import IngestResult
from mycelium.pipelines.query import QueryResult
from mycelium.server.app import create_app


@dataclass
class _RunnerState:
    running: bool = True


class _FakeClient:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.connected = True

    async def ingest(
        self,
        content: FactContent,
        source_type: SourceType,
        tags: list[str] | None = None,
        derived_from: list[object] | None = None,
        metadata: dict[str, object] | None = None,
        initial_confidence: float | None = None,
    ) -> IngestResult:
        del tags, derived_from, metadata, initial_confidence
        fact = Fact(
            id=uuid4(),
            content=content,
            source_agent_id=self.agent_id,
            source_type=source_type,
            confidence=0.7,
            trust_score=0.6,
            valid_from=datetime.now(UTC),
            tags=[],
        )
        return IngestResult(fact=fact)

    async def query(
        self, question: str, filters: object | None = None, limit: int | None = None,
    ) -> list[QueryResult]:
        del question, filters, limit
        fact = Fact(
            id=uuid4(),
            content=FactContent(subject="api", predicate="has_status", object="healthy"),
            source_agent_id=self.agent_id,
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.7,
            trust_score=0.6,
            valid_from=datetime.now(UTC),
            tags=["api"],
        )
        return [QueryResult(fact=fact, score=0.9, similarity=0.8)]

    async def feedback(
        self,
        fact_id: object,
        signal: FeedbackSignal,
        reason: str | None = None,
    ) -> FeedbackResult:
        del reason
        return FeedbackResult(
            fact_id=fact_id,  # type: ignore[arg-type]
            signal=signal,
            confidence_delta=-0.2,
            trust_delta=-0.03,
            verification_status=VerificationStatus.FAILED,
        )


@dataclass
class _ExtractionWorkspaceStats:
    workspace_key: str
    files_seen: int
    files_processed: int
    facts_extracted: int
    facts_ingested: int
    facts_skipped: int
    facts_failed: int


@dataclass
class _ExtractionResult:
    started_at: datetime
    finished_at: datetime | None
    expired_memory_migration_facts: int
    workspaces: list[_ExtractionWorkspaceStats]
    errors: list[str]

    @property
    def total_files_seen(self) -> int:
        return sum(item.files_seen for item in self.workspaces)

    @property
    def total_files_processed(self) -> int:
        return sum(item.files_processed for item in self.workspaces)

    @property
    def total_facts_extracted(self) -> int:
        return sum(item.facts_extracted for item in self.workspaces)

    @property
    def total_facts_ingested(self) -> int:
        return sum(item.facts_ingested for item in self.workspaces)

    @property
    def total_facts_skipped(self) -> int:
        return sum(item.facts_skipped for item in self.workspaces)

    @property
    def total_facts_failed(self) -> int:
        return sum(item.facts_failed for item in self.workspaces)


class _DummyState:
    def __init__(self) -> None:
        self._clients: dict[str, _FakeClient] = {}
        self.pool = object()
        self.sweeper = _RunnerState(running=True)
        self.decay_runner = _RunnerState(running=True)

    async def connect_agent(
        self, agent_id: str, role: str = "generic", subscriptions: list[Any] | None = None,
    ) -> _FakeClient:
        del role, subscriptions
        client = _FakeClient(agent_id)
        self._clients[agent_id] = client
        return client

    async def disconnect_agent(self, agent_id: str) -> None:
        self._clients.pop(agent_id, None)

    def require_client(self, agent_id: str) -> _FakeClient:
        client = self._clients.get(agent_id)
        if client is None:
            raise ValueError(f"agent '{agent_id}' is not connected")
        return client

    async def run_daily_notes_extraction(
        self,
        *,
        workspaces: list[Any] | None = None,
        expire_memory_migration_facts: bool = True,
    ) -> _ExtractionResult:
        del workspaces
        return _ExtractionResult(
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            expired_memory_migration_facts=1 if expire_memory_migration_facts else 0,
            workspaces=[
                _ExtractionWorkspaceStats(
                    workspace_key="planner",
                    files_seen=2,
                    files_processed=1,
                    facts_extracted=3,
                    facts_ingested=2,
                    facts_skipped=1,
                    facts_failed=0,
                )
            ],
            errors=[],
        )

    async def list_agent_facts(
        self,
        agent_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        active_only: bool = True,
    ) -> list[Fact]:
        del agent_id, limit, offset, active_only
        return [
            Fact(
                id=uuid4(),
                content=FactContent(
                    subject="api",
                    predicate="has_status",
                    object="healthy",
                ),
                source_agent_id="a1",
                source_type=SourceType.AGENT_EXTRACTION,
                confidence=0.7,
                trust_score=0.6,
                valid_from=datetime.now(UTC),
                tags=["api"],
            )
        ]


def _client(api_key: str = "test-key") -> TestClient:
    dummy: Any = _DummyState()
    app = create_app(
        MyceliumConfig(server_api_key=api_key),
        state=dummy,
    )
    return TestClient(app)


def test_auth_required() -> None:
    client = _client()
    response = client.post("/v1/agents/connect", json={"agent_id": "a1", "role": "test"})
    assert response.status_code == 401


def test_connect_ingest_query_roundtrip() -> None:
    client = _client()
    headers = {"Authorization": "Bearer test-key"}

    connect = client.post(
        "/v1/agents/connect",
        json={"agent_id": "a1", "role": "test"},
        headers=headers,
    )
    assert connect.status_code == 200
    assert connect.json()["connected"] is True

    ingest = client.post(
        "/v1/agents/a1/ingest",
        json={
            "content": {"subject": "api", "predicate": "has_status", "object": "healthy"},
            "source_type": "agent_extraction",
        },
        headers=headers,
    )
    assert ingest.status_code == 200
    assert ingest.json()["accepted"] is True

    query = client.post(
        "/v1/agents/a1/query",
        json={"question": "api health"},
        headers=headers,
    )
    assert query.status_code == 200
    body = query.json()
    assert len(body) == 1
    assert body[0]["fact"]["content"]["subject"] == "api"


def test_extraction_run_endpoint() -> None:
    client = _client()
    headers = {"Authorization": "Bearer test-key"}

    response = client.post(
        "/v1/extraction/run",
        json={"expire_memory_migration_facts": True},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["expired_memory_migration_facts"] == 1
    assert body["total_facts_ingested"] == 2
    assert body["workspaces"][0]["workspace_key"] == "planner"


def test_list_agent_facts_endpoint() -> None:
    client = _client()
    headers = {"Authorization": "Bearer test-key"}
    connect = client.post(
        "/v1/agents/connect",
        json={"agent_id": "a1", "role": "test"},
        headers=headers,
    )
    assert connect.status_code == 200

    response = client.get(
        "/v1/agents/a1/facts?limit=10&offset=0&active_only=true",
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["content"]["subject"] == "api"


def test_feedback_endpoint() -> None:
    client = _client()
    headers = {"Authorization": "Bearer test-key"}
    connect = client.post(
        "/v1/agents/connect",
        json={"agent_id": "a1", "role": "test"},
        headers=headers,
    )
    assert connect.status_code == 200

    response = client.post(
        "/v1/agents/a1/feedback",
        json={
            "fact_id": str(uuid4()),
            "signal": "wrong",
            "reason": "operator correction",
        },
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["signal"] == "wrong"
    assert payload["verification_status"] == "failed"
