from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from mycelium.config import MyceliumConfig
from mycelium.domain.types import Fact, FactContent, SourceType
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
        derived_from: list | None = None,
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
            valid_from=datetime.now(),
            tags=[],
        )
        return IngestResult(fact=fact)

    async def query(self, question: str, filters=None, limit=None) -> list[QueryResult]:
        del question, filters, limit
        fact = Fact(
            id=uuid4(),
            content=FactContent(subject="api", predicate="has_status", object="healthy"),
            source_agent_id=self.agent_id,
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.7,
            trust_score=0.6,
            valid_from=datetime.now(),
            tags=["api"],
        )
        return [QueryResult(fact=fact, score=0.9, similarity=0.8)]


class _DummyState:
    def __init__(self) -> None:
        self._clients: dict[str, _FakeClient] = {}
        self.pool = object()
        self.sweeper = _RunnerState(running=True)
        self.decay_runner = _RunnerState(running=True)

    async def connect_agent(self, agent_id: str, role: str = "generic", subscriptions=None):
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


def _client(api_key: str = "test-key") -> TestClient:
    app = create_app(
        MyceliumConfig(server_api_key=api_key),
        state=_DummyState(),
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
