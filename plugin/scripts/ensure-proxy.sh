#!/usr/bin/env bash
# Ensure spillover daemon is running AND ~/.claude/settings.json points its
# ANTHROPIC_BASE_URL at the local proxy.
#
# Why both:
#   * daemon must be up to serve the request
#   * settings.json is the ONLY mechanism Claude Code honors for setting
#     ANTHROPIC_BASE_URL on the main process (env file is too late, applies
#     only to subprocesses). Doc: https://code.claude.com/docs/en/hooks
#
# Idempotent. Safe to run on every SessionStart.

set -u

PORT="${SPILLOVER_PORT:-8787}"
HEALTH_URL="http://127.0.0.1:${PORT}/health"
SPILLOVER_BIN="${SPILLOVER_BIN:-spillover}"
LOG="${SPILLOVER_DAEMON_LOG:-$HOME/.spillover/daemon.log}"
SETTINGS="$HOME/.claude/settings.json"
BASE_URL="http://127.0.0.1:${PORT}"

mkdir -p "$(dirname "$LOG")"

# ---- 1. ensure daemon ----
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

# ---- 2. ensure settings.json env.ANTHROPIC_BASE_URL ----
# Use python because jq is not guaranteed on Windows / Git Bash installs.
if command -v python >/dev/null 2>&1; then
  PYBIN=python
elif command -v python3 >/dev/null 2>&1; then
  PYBIN=python3
else
  exit 0
fi

# Resolve Windows path if needed (Git Bash gets POSIX path from $HOME)
if command -v cygpath >/dev/null 2>&1; then
  SETTINGS_WIN="$(cygpath -w "$SETTINGS")"
else
  SETTINGS_WIN="$SETTINGS"
fi

"$PYBIN" - "$SETTINGS_WIN" "$BASE_URL" <<'PY'
import json
import os
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
base_url = sys.argv[2]

settings_path.parent.mkdir(parents=True, exist_ok=True)
if settings_path.exists():
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
else:
    data = {}

env = data.get("env") or {}
desired = {
    "ANTHROPIC_BASE_URL": base_url,
    "CLAUDE_CODE_AUTO_COMPACT": "0",
    "CLAUDE_CODE_DISABLE_COMPACT": "1",
    "CLAUDE_CODE_DISABLE_AUTO_COMPACT": "1",
    "DISABLE_AUTOCOMPACT": "true",
}
changed = False
for k, v in desired.items():
    if env.get(k) != v:
        env[k] = v
        changed = True

if changed:
    data["env"] = env
    tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, settings_path)
PY

exit 0
