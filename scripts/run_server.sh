#!/usr/bin/env bash
set -euo pipefail

# Load environment from secure env file
ENV_FILE="${MYCELIUM_ENV_FILE:-$HOME/.config/mycelium/env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

cd /Users/jasperbot/Projects/mycelium

exec .venv/bin/uvicorn \
  mycelium.server.app_factory:app \
  --host "${MYCELIUM_SERVER_HOST:-127.0.0.1}" \
  --port "${MYCELIUM_SERVER_PORT:-8080}" \
  --reload \
  --reload-dir src/mycelium \
  --log-level info
