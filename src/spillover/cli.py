from __future__ import annotations

import hashlib
import re
from pathlib import Path

import click
import uvicorn

from spillover.config import Config
from spillover.proxy.app import create_app
from spillover.storage.sqlite import open_project_db, project_db_path

_HEX_ID = re.compile(r"^[0-9a-f]{6,64}$")


def _resolve_pid(raw: str) -> str:
    """Mirror ProjectIdMiddleware: pass hex IDs through; sha1-hash everything else."""
    if _HEX_ID.match(raw):
        return raw
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


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
    project_id = _resolve_pid(project_id)
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
    project_id = _resolve_pid(project_id)
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
@click.option("--tasks", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--report", type=click.Path(dir_okay=False), default="bench-report.md")
@click.option(
    "--run", is_flag=True, default=False,
    help="Run benchmark against Anthropic (requires ANTHROPIC_API_KEY or OAuth)",
)
@click.option("--proxy-url", default="http://127.0.0.1:8787")
@click.option("--vanilla-url", default="https://api.anthropic.com")
@click.option(
    "--project", default=None,
    help="Override project_id for spillover runs (default: random per session)",
)
@click.option("--model", default="claude-haiku-4-5-20251001")
def bench(tasks: str, report: str, run: bool, proxy_url: str, vanilla_url: str,
          project: str | None, model: str):
    """Run the offline A/B harness OR render a markdown report from a scoring file."""
    from dataclasses import asdict

    from spillover.bench.runner import (
        main_offline_demo,
        render_ab_report,
        run_ab,
    )

    tasks_path = Path(tasks)
    report_path = Path(report)

    if not run:
        main_offline_demo(tasks_path, report_path)
        click.echo(f"wrote {report_path}")
        return

    # Live mode: resolve auth
    import json
    import os
    import uuid

    auth = os.environ.get("ANTHROPIC_API_KEY")
    if auth and not auth.startswith("Bearer "):
        auth = f"Bearer {auth}"
    if not auth:
        cred_path = Path.home() / ".claude" / ".credentials.json"
        if cred_path.exists():
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            tok = data.get("claudeAiOauth", {}).get("accessToken")
            if tok:
                auth = f"Bearer {tok}"
    if not auth:
        click.echo(
            "No auth available. Set ANTHROPIC_API_KEY or run `claude` once to populate OAuth.",
            err=True,
        )
        raise SystemExit(2)

    pid = project or hashlib.sha1(uuid.uuid4().bytes).hexdigest()
    proxy_with_proj = f"{proxy_url.rstrip('/')}/p/{pid}"

    click.echo(f"Running A/B against {len(open(tasks_path).readlines())} tasks")
    click.echo(f"  vanilla url: {vanilla_url}")
    click.echo(f"  spillover url: {proxy_with_proj}")
    click.echo(f"  project: {pid}")
    results = run_ab(tasks_path, auth, proxy_with_proj, vanilla_base_url=vanilla_url, model=model)
    report_path.write_text(render_ab_report(results), encoding="utf-8")

    # Also dump raw results for re-rendering
    raw_path = report_path.with_suffix(".jsonl")
    with raw_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")
    click.echo(f"wrote {report_path} and {raw_path}")


@main.command(name="bench-long")
@click.option("--proxy-url", default="http://127.0.0.1:8787")
@click.option("--vanilla-url", default="https://api.anthropic.com")
@click.option("--report", default="bench-long-report.md")
@click.option("--model", default="claude-haiku-4-5-20251001")
def bench_long(proxy_url: str, vanilla_url: str, report: str, model: str):
    """Run the long-conversation bench (anchor facts embedded mid-history)."""
    import json
    import os
    import uuid

    from spillover.bench.long_conversation import (
        all_scenarios,
        render_report,
        run_spillover,
        run_vanilla_truncated,
    )

    auth = os.environ.get("ANTHROPIC_API_KEY")
    if auth and not auth.startswith("Bearer "):
        auth = f"Bearer {auth}"
    if not auth:
        cred_path = Path.home() / ".claude" / ".credentials.json"
        if cred_path.exists():
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            tok = data.get("claudeAiOauth", {}).get("accessToken")
            if tok:
                auth = f"Bearer {tok}"
    if not auth:
        click.echo("No auth available.", err=True)
        raise SystemExit(2)

    pid = hashlib.sha1(uuid.uuid4().bytes).hexdigest()
    proxy_with_proj = f"{proxy_url.rstrip('/')}/p/{pid}"
    click.echo(f"project: {pid}")

    results = []
    for sc in all_scenarios():
        click.echo(f"-> scenario {sc.id}")
        results.append(run_vanilla_truncated(sc, vanilla_url, auth, model))
        results.append(run_spillover(sc, proxy_with_proj, auth, model))

    Path(report).write_text(render_report(results), encoding="utf-8")
    from dataclasses import asdict
    raw = Path(report).with_suffix(".jsonl")
    with raw.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")
    click.echo(f"wrote {report} and {raw}")


@main.command(name="bench-logic")
@click.option("--proxy-url", default="http://127.0.0.1:8787")
@click.option("--vanilla-url", default="https://api.anthropic.com")
@click.option("--report", default="bench-logic-report.md")
@click.option("--model", default="claude-haiku-4-5-20251001")
@click.option("--keep-last-n", default=8,
              help="vanilla mode: how many tail turns to keep when simulating compaction")
def bench_logic(proxy_url: str, vanilla_url: str, report: str, model: str, keep_last_n: int):
    """Run the landing-page logic-retention scenario per-detail."""
    import json
    import os
    import uuid

    from spillover.bench.landing_page_scenario import (
        LANDING_PAGE_DETAILS,
        build_landing_page_history,
        render_logic_report,
        run_logic_check,
    )

    auth = os.environ.get("ANTHROPIC_API_KEY")
    if auth and not auth.startswith("Bearer "):
        auth = f"Bearer {auth}"
    if not auth:
        cred_path = Path.home() / ".claude" / ".credentials.json"
        if cred_path.exists():
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            tok = data.get("claudeAiOauth", {}).get("accessToken")
            if tok:
                auth = f"Bearer {tok}"
    if not auth:
        click.echo("No auth available.", err=True)
        raise SystemExit(2)

    history = build_landing_page_history()
    truncated = history[-keep_last_n:]

    pid = hashlib.sha1(uuid.uuid4().bytes).hexdigest()
    proxy_with_proj = f"{proxy_url.rstrip('/')}/p/{pid}"
    click.echo(f"project: {pid}")
    click.echo(f"history: {len(history)} turns")
    click.echo(f"running {len(LANDING_PAGE_DETAILS)} detail checks per mode")

    results = []
    for d in LANDING_PAGE_DETAILS:
        click.echo(f"-> {d.name}")
        results.append(run_logic_check(truncated, d, vanilla_url, auth, model, "vanilla_truncated"))
        results.append(run_logic_check(
            history, d, proxy_with_proj, auth, model, "spillover",
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        ))

    Path(report).write_text(render_logic_report(results), encoding="utf-8")
    from dataclasses import asdict
    raw = Path(report).with_suffix(".jsonl")
    with raw.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")
    click.echo(f"wrote {report} and {raw}")


@main.command(name="bench-heavy")
@click.option("--proxy-url", default="http://127.0.0.1:8787")
@click.option("--vanilla-url", default="https://api.anthropic.com")
@click.option("--report", default="bench-heavy-report.md")
@click.option("--model", default="claude-haiku-4-5-20251001")
@click.option("--keep-last-n", default=12,
              help="vanilla mode tail-keep when simulating compaction")
def bench_heavy(proxy_url: str, vanilla_url: str, report: str, model: str, keep_last_n: int):
    """Run the 200-turn heavy stress bench against live Anthropic."""
    import hashlib
    import json
    import os
    import uuid

    from spillover.bench.heavy_stress import (
        build_heavy_history,
        render_report,
        run_spillover_full,
        run_vanilla_truncated,
    )

    auth = os.environ.get("ANTHROPIC_API_KEY")
    if auth and not auth.startswith("Bearer "):
        auth = f"Bearer {auth}"
    if not auth:
        cred_path = Path.home() / ".claude" / ".credentials.json"
        if cred_path.exists():
            data = json.loads(cred_path.read_text(encoding="utf-8"))
            tok = data.get("claudeAiOauth", {}).get("accessToken")
            if tok:
                auth = f"Bearer {tok}"
    if not auth:
        click.echo("No auth available.", err=True)
        raise SystemExit(2)

    history, anchors = build_heavy_history()
    chars = sum(len(t["content"]) for t in history)
    click.echo(f"history: {len(history)} turns, ~{chars} chars")
    click.echo("anchors at turns 5, 50, 100, 150")
    click.echo(f"vanilla keep-last-n={keep_last_n}")

    pid = hashlib.sha1(uuid.uuid4().bytes).hexdigest()
    proxy_with_proj = f"{proxy_url.rstrip('/')}/p/{pid}"
    click.echo(f"project: {pid}")

    click.echo("-> vanilla_truncated")
    v = run_vanilla_truncated(history, vanilla_url, auth, model, keep_last_n)
    click.echo(f"   {len(v.anchors_hit)}/4 anchors hit, {v.latency_ms}ms, {v.input_tokens} in tokens")  # noqa: E501

    click.echo("-> spillover (full 200 turns)")
    s = run_spillover_full(history, proxy_with_proj, auth, model)
    click.echo(f"   {len(s.anchors_hit)}/4 anchors hit, {s.latency_ms}ms, {s.input_tokens} visible / {s.real_input_tokens} real")  # noqa: E501

    results = [v, s]
    Path(report).write_text(render_report(results), encoding="utf-8")
    from dataclasses import asdict
    raw = Path(report).with_suffix(".jsonl")
    with raw.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")
    click.echo(f"wrote {report} and {raw}")


if __name__ == "__main__":
    main()
