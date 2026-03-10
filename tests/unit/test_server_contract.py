from __future__ import annotations

from dataclasses import dataclass

import pytest

pytest.importorskip("fastapi")

from mycelium.config import MyceliumConfig
from mycelium.server.app import create_app


@dataclass
class _RunnerState:
    running: bool = True


class _DummyState:
    def __init__(self) -> None:
        self.pool = object()
        self.sweeper = _RunnerState(running=True)
        self.decay_runner = _RunnerState(running=True)


def test_openapi_contains_v1_paths() -> None:
    app = create_app(MyceliumConfig(server_api_key="x"), state=_DummyState())
    schema = app.openapi()
    paths = schema["paths"]

    assert "/v1/agents/connect" in paths
    assert "/v1/agents/{agent_id}/ingest" in paths
    assert "/v1/agents/{agent_id}/query" in paths
    assert "/v1/agents/{agent_id}/resolve-conflicts" in paths
    assert "/v1/agents/{agent_id}/replay" in paths
