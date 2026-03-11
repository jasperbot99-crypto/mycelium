from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from mycelium.storage.supabase_store import SupabaseMemoryStore


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
