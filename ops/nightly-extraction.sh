#!/usr/bin/env bash
set -euo pipefail

MYCELIUM_SERVER_URL="${MYCELIUM_SERVER_URL:-http://127.0.0.1:8080}"
MYCELIUM_API_KEY="${MYCELIUM_API_KEY:-}"

HEADERS=(-H "Content-Type: application/json")
if [[ -n "${MYCELIUM_API_KEY}" ]]; then
  HEADERS+=(-H "Authorization: Bearer ${MYCELIUM_API_KEY}")
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting nightly extraction..."

curl --fail --silent --show-error \
  -X POST "${MYCELIUM_SERVER_URL}/v1/extraction/run" \
  "${HEADERS[@]}" \
  -d '{"expire_memory_migration_facts":true}'

echo ""
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Extraction complete."
