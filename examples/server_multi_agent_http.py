"""Multi-agent interaction through server API."""

from __future__ import annotations

import asyncio

import httpx


async def main() -> None:
    base = "http://127.0.0.1:8080"
    headers = {"Authorization": "Bearer change-me"}

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=30.0) as client:
        await client.post("/v1/agents/connect", json={"agent_id": "agent-a", "role": "coder"})
        await client.post("/v1/agents/connect", json={"agent_id": "agent-b", "role": "reviewer"})

        await client.post(
            "/v1/agents/agent-a/ingest",
            json={
                "content": {
                    "subject": "api-v3",
                    "predicate": "has_status",
                    "object": "healthy",
                },
                "source_type": "agent_extraction",
                "tags": ["api.v3"],
            },
        )

        response = await client.post(
            "/v1/agents/agent-b/query",
            json={"question": "api v3 status"},
        )
        print(response.json())


if __name__ == "__main__":
    asyncio.run(main())
