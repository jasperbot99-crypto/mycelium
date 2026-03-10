"""Conflict creation + resolution flow via Python client."""

from __future__ import annotations

import asyncio

from mycelium.client.client import MyceliumClient
from mycelium.config import MyceliumConfig
from mycelium.domain.types import FactContent, SourceType
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.storage.memory import (
    InMemoryAgentRepository,
    InMemoryConflictRepository,
    InMemoryEventLog,
    InMemoryFactRepository,
    InMemoryRelationRepository,
    InMemorySubscriptionRepository,
)


async def main() -> None:
    fact_repo = InMemoryFactRepository()
    agent_repo = InMemoryAgentRepository()
    conflict_repo = InMemoryConflictRepository()
    relation_repo = InMemoryRelationRepository()

    client = MyceliumClient(
        agent_id="resolver",
        role="ops",
        config=MyceliumConfig(),
        fact_repo=fact_repo,
        agent_repo=agent_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        subscription_repo=InMemorySubscriptionRepository(),
        event_log=InMemoryEventLog(),
        embedding_provider=MockEmbeddingProvider(dimension=64),
    )
    await client.connect()

    await client.ingest(
        FactContent(subject="db-main", predicate="has_status", object="down"),
        SourceType.AGENT_EXTRACTION,
    )
    await client.ingest(
        FactContent(subject="db-main", predicate="has_status", object="up"),
        SourceType.AGENT_INFERENCE,
    )

    resolutions = await client.resolve_conflicts(limit=100)
    print(resolutions)


if __name__ == "__main__":
    asyncio.run(main())
