#!/usr/bin/env bash
# Tail logs of background listener.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f logs/listener.log ]]; then
  echo "logs/listener.log not found. Did you start with ./scripts/up.sh --bg?"
  exit 1
fi
exec tail -f logs/listener.log
