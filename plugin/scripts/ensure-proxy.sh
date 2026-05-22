#!/usr/bin/env bash
# Ensure spillover daemon is up AND ~/.claude/settings.json points
# ANTHROPIC_BASE_URL at the local proxy. Idempotent.
#
# Why both:
#   * daemon must be up to serve the request
#   * settings.json is the only mechanism Claude Code honors for setting
#     ANTHROPIC_BASE_URL on the main process (env file is too late, applies
#     only to subprocesses). See: https://code.claude.com/docs/en/hooks
#
# If anything breaks, user can run `spillover route off` to restore direct
# routing without editing JSON by hand.

set -u

PORT="${SPILLOVER_PORT:-8787}"
HEALTH_URL="http://127.0.0.1:${PORT}/health"
SPILLOVER_BIN="${SPILLOVER_BIN:-spillover}"
LOG="${SPILLOVER_DAEMON_LOG:-$HOME/.spillover/daemon.log}"
BASE_URL="http://127.0.0.1:${PORT}"

mkdir -p "$(dirname "$LOG")"

# ---- 1. ensure daemon up ----
if ! curl -sf --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
  if command -v "$SPILLOVER_BIN" >/dev/null 2>&1; then
    nohup "$SPILLOVER_BIN" up >>"$LOG" 2>&1 &
    disown 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      curl -sf --max-time 1 "$HEALTH_URL" >/dev/null 2>&1 && break
      sleep 1
    done
  fi
fi

# Only write settings.json if daemon actually came up. Otherwise we'd route
# Claude Code at a dead port and break every subsequent session.
if ! curl -sf --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
  exit 0
fi

# ---- 2. ensure settings.json env.ANTHROPIC_BASE_URL ----
if command -v "$SPILLOVER_BIN" >/dev/null 2>&1; then
  "$SPILLOVER_BIN" route on --port "$PORT" >/dev/null 2>&1 || true
fi

exit 0
