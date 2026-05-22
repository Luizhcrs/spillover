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
@click.argument("project_id")
@click.option("--all", "purge_all", is_flag=True, default=False,
              help="Delete EVERY episode for the project (not just rescued ones).")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation.")
def purge(project_id: str, purge_all: bool, yes: bool):
    """Delete episodes from a project's archive.

    Default removes only compaction_rescued=1 rows (false-positive rescues from
    the legacy detect_compaction window). Use --all to wipe the whole archive.
    """
    project_id = _resolve_pid(project_id)
    config = Config.from_env()
    path = project_db_path(config.db_root, project_id)
    if not path.exists():
        click.echo(f"project {project_id}: no archive found")
        return
    if not yes:
        scope = "ALL episodes" if purge_all else "rescued episodes only"
        click.confirm(f"Delete {scope} for project {project_id}?", abort=True)
    db = open_project_db(config.db_root, project_id)
    try:
        if purge_all:
            n = db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            db.execute("DELETE FROM episodes")
            db.execute("DELETE FROM vec_episodes")
            db.execute("DELETE FROM seen_turns")
            db.commit()
            click.echo(f"deleted {n} episodes + reset seen_turns")
        else:
            n = db.execute(
                "SELECT COUNT(*) FROM episodes WHERE compaction_rescued=1"
            ).fetchone()[0]
            db.execute(
                "DELETE FROM vec_episodes WHERE episode_id IN ("
                "SELECT id FROM episodes WHERE compaction_rescued=1"
                ")"
            )
            db.execute("DELETE FROM episodes WHERE compaction_rescued=1")
            db.execute("DELETE FROM seen_turns")
            db.commit()
            click.echo(f"deleted {n} rescued episodes + reset seen_turns")
    finally:
        db.close()


@main.group()
def route():
    """Toggle Claude Code routing through the spillover proxy.

    Writes (or removes) `env.ANTHROPIC_BASE_URL` in `~/.claude/settings.json`.
    This is the canonical (and only) way to make Claude Code's main process
    use the proxy; SessionStart hooks cannot mutate the parent process env
    (per official docs at https://code.claude.com/docs/en/hooks).
    """


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


_ROUTE_KEYS = (
    "ANTHROPIC_BASE_URL",
    # Current Claude Code (>=2.1.x) auto-compact knobs:
    "DISABLE_AUTO_COMPACT",           # canonical disable flag
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",  # set to 100 so compaction never trips
    # Legacy variants — keep cleaning them on `route off` for old installs:
    "CLAUDE_CODE_AUTO_COMPACT",
    "CLAUDE_CODE_DISABLE_COMPACT",
    "CLAUDE_CODE_DISABLE_AUTO_COMPACT",
    "DISABLE_AUTOCOMPACT",
    "SPILLOVER_PASSIVE",
)


def _load_settings() -> tuple[Path, dict]:
    import json
    p = _settings_path()
    if p.exists():
        try:
            return p, json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return p, {}
    return p, {}


def _save_settings(path: Path, data: dict) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    import os as _os
    _os.replace(tmp, path)


@route.command("on")
@click.option("--port", default=None, type=int, help="Override SPILLOVER_PORT")
@click.option(
    "--passive/--active", default=False,
    help="Passive: proxy never mutates the outbound request (no LTM injection, "
         "no rescue, no compact intercept). Anthropic sees the same payload "
         "Claude Code would send direct, so rate-limit behavior is identical "
         "to running without the proxy. Memory still accumulates from observed "
         "responses. Default: active.",
)
def route_on(port: int | None, passive: bool):
    """Point Claude Code at the local spillover proxy."""
    import os
    p = port if port is not None else int(os.environ.get("SPILLOVER_PORT", "8787"))
    base_url = f"http://127.0.0.1:{p}"
    path, data = _load_settings()
    env = data.get("env") or {}
    env["ANTHROPIC_BASE_URL"] = base_url
    # Canonical disable flags for current Claude Code (>=2.1.x):
    env["DISABLE_AUTO_COMPACT"] = "1"
    env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = "100"
    # Drop legacy variants if previously written (some users keep them around):
    for legacy in (
        "CLAUDE_CODE_AUTO_COMPACT",
        "CLAUDE_CODE_DISABLE_COMPACT",
        "CLAUDE_CODE_DISABLE_AUTO_COMPACT",
        "DISABLE_AUTOCOMPACT",
    ):
        env.pop(legacy, None)
    if passive:
        env["SPILLOVER_PASSIVE"] = "1"
    else:
        env.pop("SPILLOVER_PASSIVE", None)
    data["env"] = env
    _save_settings(path, data)
    mode = "PASSIVE (observe-only)" if passive else "ACTIVE (full memory pipeline)"
    click.echo(f"routed: Claude Code -> {base_url}  [{mode}]")
    click.echo("restart your `claude` sessions for it to take effect.")


@route.command("off")
def route_off():
    """Restore Claude Code's direct connection to api.anthropic.com."""
    path, data = _load_settings()
    env = data.get("env") or {}
    removed = []
    for k in _ROUTE_KEYS:
        if k in env:
            del env[k]
            removed.append(k)
    if env:
        data["env"] = env
    else:
        data.pop("env", None)
    _save_settings(path, data)
    if removed:
        click.echo(f"removed: {', '.join(removed)}")
    click.echo("Claude Code now talks directly to api.anthropic.com.")
    click.echo("restart your `claude` sessions for it to take effect.")


@route.command("status")
def route_status():
    """Show current Claude Code routing config."""
    path, data = _load_settings()
    env = data.get("env") or {}
    base = env.get("ANTHROPIC_BASE_URL")
    if base:
        click.echo(f"routed -> {base}")
    else:
        click.echo("not routed (direct to api.anthropic.com)")
    for k in _ROUTE_KEYS[1:]:
        v = env.get(k)
        if v is not None:
            click.echo(f"  {k}={v}")


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


@main.command(name="bench-frontend")
@click.option("--proxy-url", default="http://127.0.0.1:8787")
@click.option("--vanilla-url", default="https://api.anthropic.com")
@click.option("--report", default="bench-frontend-report.md")
@click.option("--out-dir", default="docs/eval/frontend-ab/")
@click.option("--model", default="claude-haiku-4-5-20251001")
@click.option("--keep-last-n", default=8)
def bench_frontend(proxy_url: str, vanilla_url: str, report: str, out_dir: str,
                   model: str, keep_last_n: int):
    """Roda A/B do app de delivery: vanilla truncado vs spillover full."""
    import hashlib
    import json
    import os
    import uuid

    from spillover.bench.frontend_ab import (
        _build_history,
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

    history = _build_history()
    chars = sum(len(t["content"]) for t in history)
    click.echo(f"history: {len(history)} turnos, ~{chars} chars")

    pid = hashlib.sha1(uuid.uuid4().bytes).hexdigest()
    proxy_with_proj = f"{proxy_url.rstrip('/')}/p/{pid}"
    click.echo(f"project: {pid}")

    click.echo("-> vanilla_truncated")
    v = run_vanilla_truncated(history, vanilla_url, auth, model, keep_last_n)
    click.echo(
        f"   {len(v.anchors_hit)}/{len(v.anchors_hit) + len(v.anchors_missed)} anchors, "  # noqa: E501
        f"{v.latency_ms}ms, {v.input_tokens} in / {v.output_tokens} out, "
        f"{len(v.html_out)} chars html"
    )

    click.echo("-> spillover (full history)")
    s = run_spillover_full(history, proxy_with_proj, auth, model)
    click.echo(
        f"   {len(s.anchors_hit)}/{len(s.anchors_hit) + len(s.anchors_missed)} anchors, "  # noqa: E501
        f"{s.latency_ms}ms, {s.input_tokens} visible / {s.real_input_tokens} real, "
        f"{len(s.html_out)} chars html"
    )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "vanilla.html").write_text(v.html_out, encoding="utf-8")
    (out / "spillover.html").write_text(s.html_out, encoding="utf-8")

    results = [v, s]
    Path(report).write_text(render_report(results), encoding="utf-8")
    from dataclasses import asdict
    raw = Path(report).with_suffix(".jsonl")
    with raw.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")
    click.echo(f"wrote {report}, {raw}, {out}/vanilla.html, {out}/spillover.html")


@main.command(name="bench-barbershop")
@click.option("--proxy-url", default="http://127.0.0.1:8787")
@click.option("--vanilla-url", default="https://api.anthropic.com")
@click.option("--report", default="bench-barbershop-report.md")
@click.option("--out-dir", default="docs/eval/barbershop-ab/")
@click.option("--model", default="claude-haiku-4-5-20251001")
@click.option("--keep-last-n", default=8)
def bench_barbershop(proxy_url: str, vanilla_url: str, report: str, out_dir: str,
                     model: str, keep_last_n: int):
    """A/B do sistema barbearia: vanilla truncado vs spillover full."""
    import hashlib
    import json
    import os
    import uuid

    from spillover.bench.barbershop_ab import (
        _build_history,
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

    history = _build_history()
    chars = sum(len(t["content"]) for t in history)
    click.echo(f"history: {len(history)} turnos, ~{chars} chars")

    pid = hashlib.sha1(uuid.uuid4().bytes).hexdigest()
    proxy_with_proj = f"{proxy_url.rstrip('/')}/p/{pid}"
    click.echo(f"project: {pid}")

    click.echo("-> vanilla_truncated")
    v = run_vanilla_truncated(history, vanilla_url, auth, model, keep_last_n)
    click.echo(
        f"   {len(v.anchors_hit)}/{len(v.anchors_hit) + len(v.anchors_missed)} anchors, "  # noqa: E501
        f"files {list(v.files_extracted.keys())}, "
        f"{v.latency_ms}ms, {v.input_tokens} in / {v.output_tokens} out"
    )

    click.echo("-> spillover (full history)")
    s = run_spillover_full(history, proxy_with_proj, auth, model)
    click.echo(
        f"   {len(s.anchors_hit)}/{len(s.anchors_hit) + len(s.anchors_missed)} anchors, "  # noqa: E501
        f"files {list(s.files_extracted.keys())}, "
        f"{s.latency_ms}ms, {s.input_tokens} visible / {s.real_input_tokens} real"
    )

    out = Path(out_dir)
    (out / "vanilla").mkdir(parents=True, exist_ok=True)
    (out / "spillover").mkdir(parents=True, exist_ok=True)
    (out / "vanilla" / "raw_output.txt").write_text(v.output, encoding="utf-8")
    (out / "spillover" / "raw_output.txt").write_text(s.output, encoding="utf-8")
    for name, content in v.files_extracted.items():
        (out / "vanilla" / name).write_text(content, encoding="utf-8")
    for name, content in s.files_extracted.items():
        (out / "spillover" / name).write_text(content, encoding="utf-8")

    Path(report).write_text(render_report([v, s]), encoding="utf-8")
    from dataclasses import asdict
    raw = Path(report).with_suffix(".jsonl")
    with raw.open("w", encoding="utf-8") as f:
        for r in [v, s]:
            d = asdict(r)
            d.pop("files_extracted", None)
            f.write(json.dumps(d) + "\n")
    click.echo(f"wrote {report}, {raw}, {out}/vanilla/*, {out}/spillover/*")


@main.command(name="bench-cc-realistic")
@click.option("--proxy-url", default="http://127.0.0.1:8787")
@click.option("--vanilla-url", default="https://api.anthropic.com")
@click.option("--report", default="bench-cc-realistic-report.md")
@click.option("--out-dir", default="docs/eval/cc-realistic-ab/")
@click.option("--model", default="claude-haiku-4-5-20251001")
@click.option("--turns", default=500, type=int)
@click.option("--compaction-threshold-turns", default=80, type=int,
              help="vanilla: a cada N turnos, dispara compaction")
@click.option("--target-summary-tokens", default=400, type=int)
@click.option("--keep-tail-turns", default=20, type=int)
def bench_cc_realistic(proxy_url: str, vanilla_url: str, report: str, out_dir: str,
                       model: str, turns: int, compaction_threshold_turns: int,
                       target_summary_tokens: int, keep_tail_turns: int):
    """A/B contra Claude Code REAL (com compaction via LLM summary)."""
    import hashlib
    import json
    import os
    import uuid

    from spillover.bench.cc_realistic_ab import (
        _build_long_history,
        render_report,
        run_cc_realistic,
        run_spillover_full,
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

    history = _build_long_history(turns)
    chars = sum(len(t["content"]) for t in history)
    click.echo(f"history: {len(history)} turnos, ~{chars} chars")

    pid = hashlib.sha1(uuid.uuid4().bytes).hexdigest()
    proxy_with_proj = f"{proxy_url.rstrip('/')}/p/{pid}"
    click.echo(f"project: {pid}")

    click.echo(f"-> cc_realistic (compaction a cada {compaction_threshold_turns} turnos)")
    v = run_cc_realistic(
        history, vanilla_url, auth, model,
        compaction_threshold_turns=compaction_threshold_turns,
        target_summary_tokens=target_summary_tokens,
        keep_tail_turns=keep_tail_turns,
    )
    click.echo(
        f"   {len(v.anchors_hit)}/{len(v.anchors_hit) + len(v.anchors_missed)} anchors, "  # noqa: E501
        f"{v.compaction_events} compactions, "
        f"${v.total_cost_usd_est:.4f}, {v.latency_ms}ms"
    )

    click.echo("-> spillover (full history)")
    s = run_spillover_full(history, proxy_with_proj, auth, model)
    click.echo(
        f"   {len(s.anchors_hit)}/{len(s.anchors_hit) + len(s.anchors_missed)} anchors, "  # noqa: E501
        f"{s.real_input_tokens} real / {s.input_tokens_final} visible, "
        f"${s.total_cost_usd_est:.4f}, {s.latency_ms}ms"
    )

    out = Path(out_dir)
    (out / "vanilla").mkdir(parents=True, exist_ok=True)
    (out / "spillover").mkdir(parents=True, exist_ok=True)
    (out / "vanilla" / "raw_output.txt").write_text(v.output, encoding="utf-8")
    (out / "spillover" / "raw_output.txt").write_text(s.output, encoding="utf-8")
    for name, content in v.files_extracted.items():
        (out / "vanilla" / name).write_text(content, encoding="utf-8")
    for name, content in s.files_extracted.items():
        (out / "spillover" / name).write_text(content, encoding="utf-8")

    Path(report).write_text(render_report([v, s]), encoding="utf-8")
    from dataclasses import asdict
    raw = Path(report).with_suffix(".jsonl")
    with raw.open("w", encoding="utf-8") as f:
        for r in [v, s]:
            d = asdict(r)
            d.pop("files_extracted", None)
            f.write(json.dumps(d) + "\n")
    click.echo(f"wrote {report}, {raw}, {out}/vanilla/*, {out}/spillover/*")


if __name__ == "__main__":
    main()
