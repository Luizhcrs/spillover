from __future__ import annotations

# Env vars known to disable client-side context compaction per CLI.
# Documented intent — actual flag names may evolve with CLI versions;
# the wrapper sets ALL of these so we cover historical variants.

CC_DISABLE_ENV: dict[str, str] = {
    "CLAUDE_CODE_AUTO_COMPACT": "0",
    "CLAUDE_CODE_DISABLE_COMPACT": "1",
    "CLAUDE_CODE_DISABLE_AUTO_COMPACT": "1",
}

CODEX_DISABLE_ENV: dict[str, str] = {
    "CODEX_DISABLE_COMPACT": "1",
}

DISABLE_ENV_BY_CLI: dict[str, dict[str, str]] = {
    "cc": CC_DISABLE_ENV,
    "claude-code": CC_DISABLE_ENV,
    "codex": CODEX_DISABLE_ENV,
}


def env_for(cli_name: str) -> dict[str, str]:
    """Return env vars to set when wrapping the named CLI."""
    return DISABLE_ENV_BY_CLI.get(cli_name, {})
