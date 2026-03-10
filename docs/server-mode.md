# Server Mode

## Purpose

Server mode runs the same Mycelium core logic behind a REST API for multi-process or multi-machine deployments.

## Run

```bash
export MYCELIUM_DATABASE_URL="postgresql://localhost:5432/mycelium_dev"
export OPENAI_API_KEY="..."
export MYCELIUM_SERVER_API_KEY="change-me"
mycelium-server
```

Default bind: `127.0.0.1:8080`

## Configuration

Environment variables:

- `MYCELIUM_DATABASE_URL`
- `OPENAI_API_KEY` (unless custom embedding provider is injected)
- `MYCELIUM_SERVER_API_KEY`
- `MYCELIUM_SERVER_HOST`
- `MYCELIUM_SERVER_PORT`
- `MYCELIUM_SERVER_REQUEST_TIMEOUT_S`
- `MYCELIUM_SERVER_CORS_ORIGINS` (comma-separated)

## Auth

All `/v1/*` endpoints require `Authorization: Bearer <MYCELIUM_SERVER_API_KEY>`.

## Lifecycle

On startup server mode:

- creates shared Postgres pool + repositories
- starts contradiction sweeper
- starts decay runner

On shutdown server mode:

- disconnects connected agents
- stops background loops
- closes embedding provider (if supported)
- closes pool
