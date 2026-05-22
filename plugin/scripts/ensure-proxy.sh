#!/usr/bin/env bash
# Ensure the spillover daemon is up. NO global settings.json mutation.
#
# Auto-rewriting ANTHROPIC_BASE_URL globally turned out to be invasive: if
# the daemon ever degrades, ALL Claude Code sessions break until the user
# manually edits settings.json. Opt-in via `spillover route --on` instead.

set -u

PORT="${SPILLOVER_PORT:-8787}"
HEALTH_URL="http://127.0.0.1:${PORT}/health"
SPILLOVER_BIN="${SPILLOVER_BIN:-spillover}"
LOG="${SPILLOVER_DAEMON_LOG:-$HOME/.spillover/daemon.log}"

mkdir -p "$(dirname "$LOG")"

if curl -sf --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
  exit 0
fi

if ! command -v "$SPILLOVER_BIN" >/dev/null 2>&1; then
  exit 0
fi

nohup "$SPILLOVER_BIN" up >>"$LOG" 2>&1 &
disown 2>/dev/null || true

for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -sf --max-time 1 "$HEALTH_URL" >/dev/null 2>&1 && break
  sleep 1
done

exit 0
