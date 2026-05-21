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
@click.option("--proxy", default=None)
@click.option("--project", default=None)
@click.pass_context
def main(ctx, proxy: str | None, project: str | None):
    """Launch Continue.dev with spillover wired in."""
    config = Config.from_env()
    cwd = Path.cwd().resolve()
    project_id = project or hashlib.sha1(str(cwd).encode("utf-8")).hexdigest()
    proxy_url = proxy or f"http://127.0.0.1:{config.port}/p/{project_id}"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = proxy_url
    env["OPENAI_BASE_URL"] = proxy_url
    env.update(env_for("continue"))
    env["SPILLOVER_PROJECT_ID"] = project_id

    cmd = ["continue", *ctx.args]
    click.echo(f"spillover-continue: proxy={proxy_url} project={project_id}")
    completed = subprocess.run(cmd, env=env, check=False)
    sys.exit(completed.returncode)
