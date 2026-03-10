# Troubleshooting

## `No embedding provider configured`

Set either:

- `MyceliumConfig.embedding_provider` programmatically, or
- `OPENAI_API_KEY` for server startup.

## `agent '<id>' is not connected`

Call `POST /v1/agents/connect` before ingest/query/correct/verify operations.

## `/ready` returns 503

Readiness requires:

- active database pool
- contradiction sweeper running
- decay runner running

Check startup logs for early failures.

## Auth errors

- `401`: missing header or wrong auth scheme. Use `Authorization: Bearer ...`
- `403`: wrong token value.

## Integration tests failing in sandbox

Some environments block local DB sockets. Run integration tests locally with:

```bash
pytest tests/integration/
```
