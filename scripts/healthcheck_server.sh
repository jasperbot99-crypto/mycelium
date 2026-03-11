#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${MYCELIUM_SERVER_URL:-http://127.0.0.1:8080}"

curl -fsS "$BASE_URL/health" >/dev/null
curl -fsS "$BASE_URL/ready" >/dev/null
curl -fsS "$BASE_URL/version" >/dev/null

echo "healthcheck ok: $BASE_URL"
