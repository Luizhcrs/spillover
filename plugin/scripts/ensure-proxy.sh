#!/usr/bin/env bash
# Ensure the spillover daemon is listening on its configured port.
# Idempotent: no-op when proxy already up. Spawns detached daemon otherwise.
#
# Honors SPILLOVER_PORT (default 8787) and SPILLOVER_DAEMON_LOG (default
# ~/.spillover/daemon.log). Detached so the daemon survives the hook return.

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
  echo '{"continue":true,"suppressOutput":true,"warning":"spillover binary not found on PATH; install with: pip install spillover"}' >&2
  exit 0
fi

# Detach so the hook returns immediately and the daemon outlives this shell.
nohup "$SPILLOVER_BIN" up >>"$LOG" 2>&1 &
disown 2>/dev/null || true

# Wait briefly for /health, but don't block the hook past its timeout.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

exit 0
