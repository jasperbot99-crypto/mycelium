"""End-to-end integration: plugin-style HTTP flow through server into Postgres."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from mycelium.config import MyceliumConfig
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.server.app import create_app

TEST_DATABASE_URL = os.environ.get(
    "MYCELIUM_TEST_DATABASE_URL",
    "postgresql://localhost:5432/mycelium_test",
)


@pytest.mark.integration
def test_plugin_http_roundtrip_to_postgres(clean_db: None) -> None:
    """Covers plugin -> /ingest/raw -> server pipelines -> Postgres -> /query."""
    del clean_db
    config = MyceliumConfig(
        database_url=TEST_DATABASE_URL,
        embedding_provider=MockEmbeddingProvider(dimension=1536),
        server_api_key="test-key",
        contradiction_sweep_interval_s=3600,
        decay_cycle_interval_hours=24,
    )
    app = create_app(config)
    headers = {"Authorization": "Bearer test-key"}

    with TestClient(app) as client:
        connect = client.post(
            "/v1/agents/connect",
            json={"agent_id": "openclaw-plugin", "role": "integration-test"},
            headers=headers,
        )
        assert connect.status_code == 200
        assert connect.json()["connected"] is True

        raw_ingest = client.post(
            "/v1/agents/openclaw-plugin/ingest/raw",
            json={
                "raw_text": (
                    "[jasper-code] [decision] Switched from registerHook to api.on() "
                    "for typed plugin hooks"
                ),
                "source_agent_id": "jasper-code",
            },
            headers=headers,
        )
        assert raw_ingest.status_code == 200
        ingest_body = raw_ingest.json()
        assert ingest_body["accepted"] is True
        assert ingest_body["parsed_predicate"] == "decision"

        query = client.post(
            "/v1/agents/openclaw-plugin/query",
            json={"question": "registerHook api.on typed plugin hooks", "limit": 5},
            headers=headers,
        )
        assert query.status_code == 200
        results = query.json()
        assert len(results) >= 1
        assert results[0]["fact"]["content"]["predicate"] == "decision"
