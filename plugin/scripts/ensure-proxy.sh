#!/usr/bin/env bash
# Ensure spillover daemon is up AND env routing is configured.
#
# Two layers of routing:
#   * Global ~/.claude/settings.json: ANTHROPIC_BASE_URL -> http://127.0.0.1:PORT
#     (fallback / first-ever-session)
#   * Per-project <cwd>/.claude/settings.local.json: ANTHROPIC_BASE_URL -> .../p/<sha1(cwd)>
#     (isolates memory per project; gitignored by convention)
#
# CC reads settings on boot, so the per-project file only takes effect on the
# NEXT `claude` invocation in that cwd. The hook still writes it eagerly so
# the very first session bootstraps isolation on the second run.

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

# Only continue if daemon is alive. Otherwise we'd route at a dead port.
if ! curl -sf --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
  exit 0
fi

# ---- 2. global fallback routing ----
if command -v "$SPILLOVER_BIN" >/dev/null 2>&1; then
  "$SPILLOVER_BIN" route on --port "$PORT" >/dev/null 2>&1 || true
fi

# ---- 3. per-project isolation via settings.local.json ----
CWD="${CLAUDE_PROJECT_DIR:-$PWD}"
[ -z "$CWD" ] && exit 0

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  exit 0
fi

PROJECT_HASH=$(printf '%s' "$CWD" | "$PY" -c 'import sys,hashlib;print(hashlib.sha1(sys.stdin.buffer.read()).hexdigest())' 2>/dev/null)
[ -z "$PROJECT_HASH" ] && exit 0

TARGET_URL="${BASE_URL}/p/${PROJECT_HASH}"
SETTINGS_DIR="${CWD}/.claude"
SETTINGS_FILE="${SETTINGS_DIR}/settings.local.json"

mkdir -p "$SETTINGS_DIR" 2>/dev/null || true

CHANGED=$(SPILLOVER_SETTINGS_FILE="$SETTINGS_FILE" SPILLOVER_TARGET_URL="$TARGET_URL" "$PY" <<'PYEOF' 2>/dev/null
import json, os, sys
p = os.environ["SPILLOVER_SETTINGS_FILE"]
url = os.environ["SPILLOVER_TARGET_URL"]
try:
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
except Exception:
    d = {}
env = d.setdefault("env", {})
if env.get("ANTHROPIC_BASE_URL") == url:
    print("nochange")
    sys.exit(0)
env["ANTHROPIC_BASE_URL"] = url
env.setdefault("DISABLE_AUTO_COMPACT", "1")
env.setdefault("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "100")
os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, "w", encoding="utf-8") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
print("written")
PYEOF
)

if [ "$CHANGED" = "written" ]; then
  echo "spillover: project_id pinned to ${PROJECT_HASH:0:12} (restart 'claude' to apply isolation)" >&2
fi

exit 0
