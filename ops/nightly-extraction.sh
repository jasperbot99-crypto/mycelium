#!/usr/bin/env bash
set -euo pipefail

MYCELIUM_SERVER_URL="${MYCELIUM_SERVER_URL:-http://127.0.0.1:8080}"
MYCELIUM_API_KEY="${MYCELIUM_API_KEY:-}"

if [[ -z "${MYCELIUM_API_KEY}" ]]; then
  echo "MYCELIUM_API_KEY is required"
  exit 1
fi

curl --fail --silent --show-error \
  -X POST "${MYCELIUM_SERVER_URL}/v1/extraction/run" \
  -H "Authorization: Bearer ${MYCELIUM_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"expire_memory_migration_facts":true}'
