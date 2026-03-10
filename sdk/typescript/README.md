# @mycelium/sdk

TypeScript HTTP SDK for Mycelium server mode.

## Install

```bash
npm install @mycelium/sdk
```

## Usage

```ts
import { MyceliumHttpClient } from "@mycelium/sdk";

const client = new MyceliumHttpClient({
  baseUrl: "http://127.0.0.1:8080",
  apiKey: process.env.MYCELIUM_API_KEY!,
});

await client.connect({ agent_id: "agent-ts", role: "worker" });
await client.ingest("agent-ts", {
  content: { subject: "api", predicate: "has_status", object: "healthy" },
  source_type: "agent_extraction",
});
```

## Error Handling

All non-2xx responses throw `MyceliumApiError` with `status` and `code`.

- `auth`: 401/403
- `validation`: 4xx
- `conflict`: 409
- `network`: transport/timeout failures
