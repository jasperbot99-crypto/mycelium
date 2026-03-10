# API Contract (Beta v0.x)

Base path: `/v1`

## Core endpoints

- `POST /agents/connect`
- `POST /agents/{agent_id}/disconnect`
- `POST /agents/{agent_id}/ingest`
- `POST /agents/{agent_id}/query`
- `POST /agents/{agent_id}/correct`
- `POST /agents/{agent_id}/verify`
- `POST /agents/{agent_id}/corroborate`
- `POST /agents/{agent_id}/resolve-conflicts`
- `POST /agents/{agent_id}/resolve-conflict/{conflict_id}`
- `GET /agents/{agent_id}/provenance/{fact_id}`
- `POST /agents/{agent_id}/context`
- `PUT /agents/{agent_id}/subscriptions`
- `GET /agents/{agent_id}/subscriptions`
- `POST /agents/{agent_id}/replay`
- `POST /agents/{agent_id}/ack`

## Service endpoints

- `GET /health`
- `GET /ready`
- `GET /version`

## Error model

- `401` missing/invalid auth scheme
- `403` wrong API key
- `400` validation/domain errors (`ValueError`)
- `409` runtime state conflicts (`RuntimeError`, e.g. agent not connected)
- `404` missing resources where applicable

## Compatibility policy

Beta `v0.x` follows additive-first API changes. Breaking changes require a documented deprecation notice and release note.
