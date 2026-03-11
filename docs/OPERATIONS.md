# Operations Runbook

_Updated: 2026-03-11_

## 1) End-to-end verification

Run this to verify plugin-style HTTP flow through server into Postgres:

```bash
.venv/bin/pytest tests/integration/test_server_e2e.py -m integration -q
```

This covers:
- `POST /v1/agents/{id}/ingest/raw`
- parse + ingest pipelines
- retrieval via `POST /v1/agents/{id}/query`

## 2) Nightly memory extraction (launchd)

Artifacts added:
- `scripts/nightly_memory_extraction.sh`
- `ops/launchd/com.mycelium.memory-extraction.nightly.plist.example`

Setup steps:

```bash
mkdir -p ~/Library/Logs/mycelium ~/Library/LaunchAgents
cp ops/launchd/com.mycelium.memory-extraction.nightly.plist.example \
  ~/Library/LaunchAgents/com.mycelium.memory-extraction.nightly.plist
# Edit paths + env values in the plist first.
launchctl unload ~/Library/LaunchAgents/com.mycelium.memory-extraction.nightly.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.mycelium.memory-extraction.nightly.plist
```

Required env vars for the job:
- `MYCELIUM_DATABASE_URL`
- `OPENAI_API_KEY`

## 3) Monitoring and alerts baseline

Artifacts added:
- `scripts/healthcheck_server.sh`
- `ops/launchd/com.mycelium.healthcheck.hourly.plist.example`

The healthcheck validates `/health`, `/ready`, and `/version`.
Wire alerting from non-zero exit code (launchd logs, PagerDuty, etc.).

## 4) Log rotation baseline

Artifacts added:
- `scripts/rotate_server_logs.sh`
- `ops/launchd/com.mycelium.logrotate.daily.plist.example`
- `ops/newsyslog/mycelium.conf.example`

Default retention in script is 14 days (`MYCELIUM_LOG_RETENTION_DAYS`).

## 5) API key hygiene

Do not keep placeholder values in active plist files. Use real env values or reference secure env injection.

Quick check for placeholders:

```bash
rg -n "SÆTTES HER|REPLACE_ME|YOUR_" ~/Library/LaunchAgents/com.mycelium*.plist
```
