#!/usr/bin/env bash
# Write spillover env vars into $CLAUDE_ENV_FILE so the Claude Code session
# routes through the local proxy with per-cwd project isolation.
#
# CLAUDE_ENV_FILE is the canonical mechanism documented by Claude Code for
# SessionStart hooks to inject env into the session before its first API call.

set -u

if [ -z "${CLAUDE_ENV_FILE:-}" ]; then
  exit 0
fi

PORT="${SPILLOVER_PORT:-8787}"

# Per-cwd project id = sha1(absolute working directory). Matches the wrapper
# convention and the path-routing convention in src/spillover/proxy/middleware.py
PROJECT_ID="$(printf '%s' "$PWD" | sha1sum | cut -d' ' -f1)"

{
  echo "export ANTHROPIC_BASE_URL=\"http://127.0.0.1:${PORT}/p/${PROJECT_ID}\""
  echo "export SPILLOVER_PROJECT_ID=\"${PROJECT_ID}\""
  echo "export CLAUDE_CODE_AUTO_COMPACT=0"
  echo "export CLAUDE_CODE_DISABLE_COMPACT=1"
  echo "export CLAUDE_CODE_DISABLE_AUTO_COMPACT=1"
  echo "export DISABLE_AUTOCOMPACT=true"
} >>"$CLAUDE_ENV_FILE"

exit 0
