from __future__ import annotations

import click
import uvicorn

from spillover.config import Config
from spillover.proxy.app import create_app
from spillover.storage.sqlite import open_project_db, project_db_path


@click.group()
def main():
    """spillover - transparent LLM proxy with overflow memory."""


@main.command()
@click.option("--port", default=None, type=int, help="Override listen port")
@click.option("--host", default="127.0.0.1", show_default=True)
def up(port: int | None, host: str):
    """Start the spillover proxy daemon."""
    config = Config.from_env()
    p = port if port is not None else config.port
    app = create_app(config)
    click.echo(f"spillover up at http://{host}:{p} -> {config.upstream_base_url}")
    uvicorn.run(app, host=host, port=p, log_level="info")


@main.command()
@click.argument("project_id")
def stats(project_id: str):
    """Show episode statistics for a project."""
    config = Config.from_env()
    path = project_db_path(config.db_root, project_id)
    if not path.exists():
        click.echo(f"project {project_id}: episodes: 0")
        return
    db = open_project_db(config.db_root, project_id)
    try:
        total = db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        evicted = db.execute(
            "SELECT COUNT(*) FROM episodes WHERE evicted=1"
        ).fetchone()[0]
        pinned = db.execute(
            "SELECT COUNT(*) FROM episodes WHERE pinned=1"
        ).fetchone()[0]
    finally:
        db.close()
    click.echo(f"project {project_id}: episodes: {total}")
    click.echo(f"  evicted: {evicted}")
    click.echo(f"  pinned: {pinned}")


if __name__ == "__main__":
    main()
