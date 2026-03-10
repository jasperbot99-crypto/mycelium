"""Single-process library mode example."""

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
    client = MyceliumClient(
        agent_id="example-agent",
        role="example",
        config=MyceliumConfig(),
        fact_repo=InMemoryFactRepository(),
        agent_repo=InMemoryAgentRepository(),
        conflict_repo=InMemoryConflictRepository(),
        relation_repo=InMemoryRelationRepository(),
        subscription_repo=InMemorySubscriptionRepository(),
        event_log=InMemoryEventLog(),
        embedding_provider=MockEmbeddingProvider(dimension=64),
    )
    await client.connect()
    await client.ingest(
        FactContent(subject="svc-orders", predicate="has_status", object="healthy"),
        SourceType.AGENT_EXTRACTION,
        tags=["api.orders"],
    )
    print(await client.query("orders service status"))


if __name__ == "__main__":
    asyncio.run(main())
