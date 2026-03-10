"""Shared state for Mycelium server mode."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from mycelium.client.client import MyceliumClient
from mycelium.config import MyceliumConfig, SubscriptionConfig
from mycelium.domain.types import Priority, SourceType, Subscription
from mycelium.embeddings.openai import OpenAIEmbeddingProvider
from mycelium.embeddings.protocols import EmbeddingProvider  # noqa: TC001
from mycelium.ops.logger import NullOpsLogger, OpsLogger
from mycelium.pipelines.decay import DecayConfig, DecayCycleRunner
from mycelium.pipelines.sweeper import ContradictionSweeper
from mycelium.storage.postgres.repositories import (
    PostgresAgentRepository,
    PostgresConflictRepository,
    PostgresEventLog,
    PostgresFactRepository,
    PostgresRelationRepository,
    PostgresSubscriptionRepository,
)
from mycelium.transport.in_process import InProcessTransport


class ServerState:
    """Container for shared process resources and per-agent clients."""

    def __init__(self, config: MyceliumConfig) -> None:
        self.config = config
        self.pool: asyncpg.Pool | None = None
        self.embedding_provider: EmbeddingProvider | None = None
        self.ops_logger: OpsLogger = NullOpsLogger()
        self.transport = InProcessTransport(callback_timeout=config.callback_timeout_s)

        self.fact_repo: PostgresFactRepository | None = None
        self.agent_repo: PostgresAgentRepository | None = None
        self.conflict_repo: PostgresConflictRepository | None = None
        self.relation_repo: PostgresRelationRepository | None = None
        self.subscription_repo: PostgresSubscriptionRepository | None = None
        self.event_log: PostgresEventLog | None = None

        self.sweeper: ContradictionSweeper | None = None
        self.decay_runner: DecayCycleRunner | None = None

        self._clients: dict[str, MyceliumClient] = {}

    @property
    def clients(self) -> dict[str, MyceliumClient]:
        return self._clients

    async def startup(self) -> None:
        self.pool = await asyncpg.create_pool(self.config.database_url, min_size=1, max_size=10)
        self.fact_repo = PostgresFactRepository(self.pool)
        self.agent_repo = PostgresAgentRepository(self.pool)
        self.conflict_repo = PostgresConflictRepository(self.pool)
        self.relation_repo = PostgresRelationRepository(self.pool)
        self.subscription_repo = PostgresSubscriptionRepository(self.pool)
        self.event_log = PostgresEventLog(self.pool)

        self.embedding_provider = self._build_embedding_provider()

        self.sweeper = ContradictionSweeper(
            fact_repo=self.fact_repo,
            conflict_repo=self.conflict_repo,
            relation_repo=self.relation_repo,
            contradiction_threshold=self.config.contradiction_threshold,
            corroboration_threshold=self.config.corroboration_threshold,
            sweep_interval_s=self.config.contradiction_sweep_interval_s,
        )
        self.decay_runner = DecayCycleRunner(
            fact_repo=self.fact_repo,
            config=DecayConfig(cycle_interval_hours=self.config.decay_cycle_interval_hours),
            ops_logger=self.ops_logger,
        )

        await self.sweeper.start()
        await self.decay_runner.start()

    async def shutdown(self) -> None:
        for agent_id in list(self._clients.keys()):
            client = self._clients.pop(agent_id)
            if client.connected:
                await client.disconnect()

        if self.sweeper is not None:
            await self.sweeper.stop()
        if self.decay_runner is not None:
            await self.decay_runner.stop()

        if self.embedding_provider is not None:
            close = getattr(self.embedding_provider, "close", None)
            if callable(close):
                await close()

        if self.pool is not None:
            await self.pool.close()

    async def connect_agent(
        self,
        agent_id: str,
        role: str = "generic",
        subscriptions: list[SubscriptionConfig] | None = None,
    ) -> MyceliumClient:
        existing = self._clients.get(agent_id)
        if existing is not None and existing.connected:
            return existing

        assert self.fact_repo is not None
        assert self.agent_repo is not None
        assert self.conflict_repo is not None
        assert self.relation_repo is not None
        assert self.subscription_repo is not None
        assert self.event_log is not None
        assert self.embedding_provider is not None

        cfg = replace(
            self.config,
            subscriptions=subscriptions if subscriptions is not None else [],
        )

        client = MyceliumClient(
            agent_id=agent_id,
            role=role,
            config=cfg,
            fact_repo=self.fact_repo,
            agent_repo=self.agent_repo,
            conflict_repo=self.conflict_repo,
            relation_repo=self.relation_repo,
            subscription_repo=self.subscription_repo,
            event_log=self.event_log,
            embedding_provider=self.embedding_provider,
            transport=self.transport,
            ops_logger=self.ops_logger,
        )
        await client.connect()
        self._clients[agent_id] = client
        return client

    async def disconnect_agent(self, agent_id: str) -> None:
        client = self._clients.get(agent_id)
        if client is None:
            return
        if client.connected:
            await client.disconnect()
        self._clients.pop(agent_id, None)

    def require_client(self, agent_id: str) -> MyceliumClient:
        client = self._clients.get(agent_id)
        if client is None or not client.connected:
            raise ValueError(f"agent '{agent_id}' is not connected")
        return client

    async def sync_subscriptions(
        self,
        agent_id: str,
        subs: list[SubscriptionConfig],
    ) -> list[Subscription]:
        assert self.subscription_repo is not None
        mapped = [
            Subscription(
                id=uuid4(),
                agent_id=agent_id,
                topic=sub.topic,
                priority=Priority(sub.priority),
                min_confidence=sub.min_confidence,
                source_types=(
                    [SourceType(s) for s in sub.source_types]
                    if sub.source_types
                    else None
                ),
            )
            for sub in subs
        ]
        return await self.subscription_repo.sync_subscriptions(agent_id, mapped)

    def _build_embedding_provider(self) -> EmbeddingProvider:
        if self.config.embedding_provider is not None:
            return self.config.embedding_provider
        if self.config.openai_api_key:
            return OpenAIEmbeddingProvider(api_key=self.config.openai_api_key)
        raise RuntimeError(
            "No embedding provider configured. Set MyceliumConfig.embedding_provider "
            "or openai_api_key for server mode."
        )


def subscription_configs_from_wire(raw: list[dict[str, Any]]) -> list[SubscriptionConfig]:
    return [
        SubscriptionConfig(
            topic=item["topic"],
            priority=item.get("priority", "normal"),
            min_confidence=float(item.get("min_confidence", 0.0)),
            source_types=item.get("source_types"),
        )
        for item in raw
    ]


def parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError(f"invalid UUID: {value}") from exc
