#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="${MYCELIUM_LOG_DIR:-$HOME/Library/Logs/mycelium}"
RETENTION_DAYS="${MYCELIUM_LOG_RETENTION_DAYS:-14}"

mkdir -p "$LOG_DIR"
find "$LOG_DIR" -type f -name '*.log' -mtime +"$RETENTION_DAYS" -delete

echo "rotated logs in $LOG_DIR (retention=$RETENTION_DAYS days)"
