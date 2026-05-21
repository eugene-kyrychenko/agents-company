#!/usr/bin/env bash
# Start the AI studio from a cold state.
#
#   ./scripts/up.sh            # foreground listener (Ctrl+C to stop)
#   ./scripts/up.sh --bg       # background listener (logs to ./logs/listener.log)
#
# Brings up Postgres + Redis + LiteLLM via Docker, waits for health,
# then launches the orchestrator listener.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p logs

bg=false
if [[ "${1:-}" == "--bg" ]]; then
  bg=true
fi

echo "▸ Starting Docker services (postgres, redis, litellm)..."
# Unset host vars so docker compose picks up real values from .env
# (avoids the empty-shell-var-overrides-file gotcha).
unset ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY 2>/dev/null || true
docker compose up -d postgres redis litellm

echo "▸ Waiting for LiteLLM to become healthy..."
deadline=$(( $(date +%s) + 600 ))   # 10 min max for first-time prisma migrations
while ! curl -sf --max-time 3 http://localhost:4000/health/liveliness >/dev/null 2>&1; do
  if (( $(date +%s) > deadline )); then
    echo "✗ LiteLLM did not come up in 10 min. Check 'docker compose logs litellm'."
    exit 1
  fi
  sleep 3
done
echo "✓ LiteLLM ready."

echo "▸ Launching orchestrator listener..."
if $bg; then
  nohup uv run python -m apps.orchestrator.listener \
    > logs/listener.log 2>&1 &
  echo $! > logs/listener.pid
  echo "✓ Listener started in background (pid $(cat logs/listener.pid))."
  echo "  Logs:  tail -f logs/listener.log"
  echo "  Stop:  ./scripts/down.sh"
else
  echo "  (Ctrl+C to stop. Use --bg to detach.)"
  exec uv run python -m apps.orchestrator.listener
fi
