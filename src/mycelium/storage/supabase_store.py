"""Supabase/Postgres memory store adapter (Phase 1)."""

from __future__ import annotations

import asyncpg

from mycelium.storage.base import BaseMemoryStore
from mycelium.storage.postgres.repositories import (
    PostgresAgentRepository,
    PostgresConflictRepository,
    PostgresEventLog,
    PostgresFactRepository,
    PostgresRelationRepository,
    PostgresSubscriptionRepository,
)


class SupabaseMemoryStore(BaseMemoryStore):
    """Concrete store using Supabase Postgres via asyncpg."""

    def __init__(self, database_url: str, min_size: int = 1, max_size: int = 10) -> None:
        self._database_url = database_url
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("SupabaseMemoryStore is not connected")
        return self._pool

    async def connect(self) -> None:
        if self._pool is not None:
            return

        self._pool = await asyncpg.create_pool(
            self._database_url,
            min_size=self._min_size,
            max_size=self._max_size,
        )

        self.fact_repo = PostgresFactRepository(self._pool)
        self.agent_repo = PostgresAgentRepository(self._pool)
        self.conflict_repo = PostgresConflictRepository(self._pool)
        self.relation_repo = PostgresRelationRepository(self._pool)
        self.subscription_repo = PostgresSubscriptionRepository(self._pool)
        self.event_log = PostgresEventLog(self._pool)

    async def disconnect(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None
