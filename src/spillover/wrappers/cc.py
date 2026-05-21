from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import click

from spillover.config import Config
from spillover.counter_compact.env_vars import env_for


@click.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.option(
    "--proxy",
    default=None,
    help="Override ANTHROPIC_BASE_URL (defaults to http://127.0.0.1:<port>)",
)
@click.option("--project", default=None, help="Override X-Project header value")
@click.pass_context
def main(ctx, proxy: str | None, project: str | None):
    """Launch Claude Code with spillover wired in.

    Sets ANTHROPIC_BASE_URL, the disable-compact env vars, and X-Project,
    then exec's `claude code` with any remaining args.
    """
    config = Config.from_env()
    cwd = Path.cwd().resolve()
    project_id = project or hashlib.sha1(str(cwd).encode("utf-8")).hexdigest()

    proxy_url = proxy or f"http://127.0.0.1:{config.port}"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = proxy_url
    env.update(env_for("cc"))
    # Note: claude-code does not currently accept custom request headers, so
    # X-Project must be injected another way. For now we export it as an env
    # var that user-side hooks can read; the canonical path goes through a
    # small HTTP client that does support custom headers.
    env["SPILLOVER_PROJECT_ID"] = project_id

    cmd = ["claude", "code", *ctx.args]
    click.echo(
        f"spillover-cc: ANTHROPIC_BASE_URL={proxy_url} "
        f"X-Project(env SPILLOVER_PROJECT_ID)={project_id}"
    )
    completed = subprocess.run(cmd, env=env, check=False)
    sys.exit(completed.returncode)
