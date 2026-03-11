from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mycelium.domain.types import Fact, FactContent, Priority, SourceType, Subscription
from mycelium.router import MemoryRouter
from mycelium.storage.supabase_store import SupabaseMemoryStore


@pytest.fixture
def sample_fact() -> Fact:
    return Fact(
        id=uuid4(),
        content=FactContent(subject="CI", predicate="status", object="failing"),
        source_agent_id="jasper-code",
        source_type=SourceType.AGENT_EXTRACTION,
        confidence=0.9,
        trust_score=0.8,
        valid_from=datetime.now(),
        tags=["ci.pipeline", "build"],
    )


def _sub(agent_id: str, topic: str, min_conf: float = 0.0) -> Subscription:
    return Subscription(
        id=uuid4(),
        agent_id=agent_id,
        topic=topic,
        priority=Priority.NORMAL,
        min_confidence=min_conf,
        source_types=None,
    )


class TestMemoryRouter:
    def test_routes_matching_subscriptions(self, sample_fact: Fact) -> None:
        router = MemoryRouter()
        subs = [
            _sub("agent-a", "ci.*"),
            _sub("agent-b", "weather"),
            _sub("agent-c", "*", min_conf=0.95),
        ]

        decisions = router.route_fact(sample_fact, subs)
        target_ids = [d.agent_id for d in decisions]

        assert "agent-a" in target_ids
        assert "agent-b" not in target_ids
        assert "agent-c" not in target_ids  # blocked by min confidence

    def test_deduplicates_agent_targets(self, sample_fact: Fact) -> None:
        router = MemoryRouter()
        subs = [
            _sub("agent-a", "ci.*"),
            _sub("agent-a", "build"),
        ]

        decisions = router.route_fact(sample_fact, subs)
        assert [d.agent_id for d in decisions] == ["agent-a"]


@pytest.mark.asyncio
class TestSupabaseMemoryStore:
    async def test_connect_initializes_repositories(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_pool = AsyncMock()

        async def fake_create_pool(*args: Any, **kwargs: Any) -> Any:
            return fake_pool

        monkeypatch.setattr("mycelium.storage.supabase_store.asyncpg.create_pool", fake_create_pool)

        store = SupabaseMemoryStore("postgresql://example/db")
        await store.connect()

        assert store.pool is fake_pool
        # sanity: repositories are materialized
        assert store.fact_repo is not None
        assert store.agent_repo is not None
        assert store.conflict_repo is not None
        assert store.relation_repo is not None
        assert store.subscription_repo is not None
        assert store.event_log is not None

    async def test_disconnect_closes_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_pool = AsyncMock()

        async def fake_create_pool(*args: Any, **kwargs: Any) -> Any:
            return fake_pool

        monkeypatch.setattr("mycelium.storage.supabase_store.asyncpg.create_pool", fake_create_pool)

        store = SupabaseMemoryStore("postgresql://example/db")
        await store.connect()
        await store.disconnect()

        fake_pool.close.assert_awaited_once()
