#!/bin/sh
set -e
echo "=== Buildo bot starting at $(date -u) ==="
echo "ENV: ${ENVIRONMENT:-production}"
echo "Log level: ${LOG_LEVEL:-INFO}"
exec python -m bot.main
