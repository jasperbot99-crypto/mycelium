from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

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


def test_typescript_sdk_endpoints_exist_in_openapi() -> None:
    app = create_app(MyceliumConfig(server_api_key="x"), state=_DummyState())
    schema = app.openapi()
    openapi_paths = set(schema["paths"].keys())

    client_ts = Path("sdk/typescript/src/client.ts").read_text()
    pattern = re.compile(r"`(/v1/[^`]+)`")
    raw_paths = set(pattern.findall(client_ts))

    normalized: set[str] = set()
    for path in raw_paths:
        normalized.add(path)
        normalized.add(path.replace("${agentId}", "{agent_id}"))
        normalized.add(path.replace("${agentId}", "{agent_id}").replace("${factId}", "{fact_id}"))
        normalized.add(
            path.replace("${agentId}", "{agent_id}").replace("${conflictId}", "{conflict_id}")
        )

    missing = {
        item
        for item in normalized
        if "?" not in item
        and item.startswith("/v1/")
        and "${" not in item
        and item not in openapi_paths
    }

    assert not missing, f"SDK endpoints missing in OpenAPI: {sorted(missing)}"
