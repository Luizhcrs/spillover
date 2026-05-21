from __future__ import annotations

from pathlib import Path

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
        embedded = db.execute("SELECT COUNT(*) FROM vec_episodes").fetchone()[0]
        pending = db.execute(
            "SELECT COUNT(*) FROM episodes WHERE facet_pending=1"
        ).fetchone()[0]
    finally:
        db.close()
    click.echo(f"project {project_id}: episodes: {total}")
    click.echo(f"  evicted: {evicted}")
    click.echo(f"  pinned: {pinned}")
    click.echo(f"  embedded: {embedded}")
    click.echo(f"  facet_pending: {pending}")


@main.command()
@click.argument("project_id")
@click.argument("text")
@click.option("--topk", default=None, type=int)
def query(project_id: str, text: str, topk: int | None):
    """Run the hybrid retriever ad-hoc against a project and print ranked hits."""
    from spillover.facet.embed import embed_text
    from spillover.facet.entities import extract_entities
    from spillover.retriever.fusion import rrf_fuse
    from spillover.retriever.graph import graph_walk
    from spillover.retriever.vector import vector_topk
    from spillover.storage.kuzu import open_project_kuzu

    config = Config.from_env()
    db = open_project_db(config.db_root, project_id)
    try:
        emb = embed_text(text)
        v = vector_topk(db, emb, k=config.retriever_vector_k)
        seeds = [e.name for e in extract_entities(text)][:20]
        g = []
        if seeds:
            try:
                kuzu_conn = open_project_kuzu(config.db_root, project_id)
                g = graph_walk(
                    kuzu_conn, seeds, k_hop=2, limit=config.retriever_graph_k
                )
            except Exception:
                pass
        fused = rrf_fuse(v, g)[: topk or config.retriever_topk]
        if not fused:
            click.echo("(no hits)")
            return
        for h in fused:
            click.echo(
                f"{h.episode_id}  score={h.score:.4f}  "
                f"type={h.memory_type or '-'}  source={h.source}"
            )
    finally:
        db.close()


@main.command()
@click.option("--report", type=click.Path(dir_okay=False), default="bench-report.md")
@click.option("--tasks", type=click.Path(exists=True, dir_okay=False), required=False)
def bench(report: str, tasks: str | None):
    """Run the offline A/B benchmark harness and write a markdown report."""
    import json

    from spillover.bench.ab import RunResult, render_markdown, summarize_runs

    if not tasks:
        click.echo("No --tasks file provided; nothing to run.")
        return

    raw = json.loads(Path(tasks).read_text(encoding="utf-8"))
    runs = [RunResult(**r) for r in raw]
    md = render_markdown(summarize_runs(runs))
    Path(report).write_text(md, encoding="utf-8")
    click.echo(f"wrote {report}")


if __name__ == "__main__":
    main()
