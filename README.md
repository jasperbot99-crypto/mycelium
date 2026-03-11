# Mycelium

Multi-agent memory and coordination system.

## Status

Public beta `v0.x`.

- Python library mode is production-focused for single-process usage.
- Server mode (FastAPI) + TypeScript SDK are available for distributed setups.
- API compatibility policy in beta: additive changes only where possible; breaking changes are documented in release notes.

## Install (Python)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quickstart: Library Mode

```python
import asyncio

from mycelium import MyceliumClient
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
        agent_id="demo-agent",
        role="demo",
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
        FactContent(subject="api-orders", predicate="has_status", object="healthy"),
        SourceType.AGENT_EXTRACTION,
        tags=["api.orders"],
    )
    results = await client.query("api orders status")
    print(results[0].fact.content)


asyncio.run(main())
```

## Quickstart: Server Mode

```bash
export MYCELIUM_DATABASE_URL="postgresql://localhost:5432/mycelium_dev"
export OPENAI_API_KEY="..."
export MYCELIUM_SERVER_API_KEY="change-me"
mycelium-server
```

Health endpoints:

- `GET /health`
- `GET /ready`
- `GET /version`

Primary API namespace: `/v1/*` with Bearer auth.

## Quickstart: TypeScript SDK

```bash
cd sdk/typescript
npm install
npm run build
```

```ts
import { MyceliumHttpClient } from "@mycelium/sdk";

const client = new MyceliumHttpClient({
  baseUrl: "http://127.0.0.1:8080",
  apiKey: process.env.MYCELIUM_API_KEY!,
});

await client.connect({ agent_id: "ts-agent", role: "worker" });
await client.ingest("ts-agent", {
  content: { subject: "btc", predicate: "has_price", object: "64000" },
  source_type: "agent_extraction",
});
```

## Documentation

- [SPEC.md](./SPEC.md)
- [ARCHITECTURE.md](./ARCHITECTURE.md)
- [Server Mode Guide](./docs/server-mode.md)
- [Migration Guide](./docs/MIGRATION.md)
- [API Contract](./docs/api-contract.md)
- [Troubleshooting](./docs/troubleshooting.md)
- [Examples](./examples)
- [Benchmarks](./benchmarks)

## Benchmarks

Run reproducible benchmark workloads:

```bash
python -m benchmarks.run --iterations 200
```

Outputs:

- `benchmarks/results/latest.json`
- `benchmarks/results/latest.md`

## Release Process

- See [CHANGELOG.md](./CHANGELOG.md)
- See [docs/release-notes-template.md](./docs/release-notes-template.md)

## License

MIT
