#!/usr/bin/env bash
# Stop the AI studio cleanly.
#
#   ./scripts/down.sh             # stop listener (if bg) + Docker services
#   ./scripts/down.sh --keep-db   # stop services but leave Docker containers
set -euo pipefail

cd "$(dirname "$0")/.."

keep_db=false
if [[ "${1:-}" == "--keep-db" ]]; then
  keep_db=true
fi

# Stop background listener, if any
if [[ -f logs/listener.pid ]]; then
  pid="$(cat logs/listener.pid)"
  if kill -0 "$pid" 2>/dev/null; then
    echo "▸ Stopping listener (pid $pid)..."
    kill "$pid"
    # Wait up to 10s for graceful shutdown
    for _ in $(seq 1 10); do
      if ! kill -0 "$pid" 2>/dev/null; then break; fi
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "  Listener did not exit; sending SIGKILL."
      kill -9 "$pid" 2>/dev/null || true
    fi
    echo "✓ Listener stopped."
  fi
  rm -f logs/listener.pid
fi

if $keep_db; then
  echo "▸ Leaving Docker services running (--keep-db)."
else
  echo "▸ Stopping Docker services..."
  docker compose stop
  echo "✓ Docker services stopped (data preserved)."
fi
