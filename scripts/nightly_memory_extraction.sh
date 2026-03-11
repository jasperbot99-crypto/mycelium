#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${MYCELIUM_DATABASE_URL:-}" ]]; then
  echo "MYCELIUM_DATABASE_URL is required" >&2
  exit 1
fi
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required" >&2
  exit 1
fi
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <memory-file> [<memory-file> ...]" >&2
  exit 1
fi

cmd=(.venv/bin/mycelium-migrate run --database-url "$MYCELIUM_DATABASE_URL" --source memory_file)
for file in "$@"; do
  cmd+=(--memory-file "$file")
done
cmd+=(--openai-api-key "$OPENAI_API_KEY")

"${cmd[@]}"
