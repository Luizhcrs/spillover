# spillover Plan 7: Eval Harness + Multi-Project Routing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Two threads in one branch. (a) Fix the multi-project routing limitation exposed by the v1.2.0 smoke (daemon needs `SPILLOVER_PROJECT_ID` env, only one project active per daemon) plus the `stats`/`query` CLI bug that doesn't hash the project_id like middleware does. (b) Add the evaluation infrastructure that v2 promises will land — bench runner (real A/B vs Anthropic), recall@5 dataset + harness, chaos test (SIGKILL mid-archive recovery), and a user-facing A/B comparison CLI so the value proposition is demonstrable.

End state: v1.3.0 tagged. Multi-project works without restarting the daemon. Numbers published.

---

## File structure

New files:

```
src/spillover/
  cli.py                          # MODIFIED: stats/query hash if non-hex
  proxy/app.py                    # MODIFIED: /p/<project_id>/... path-based routing
  proxy/middleware.py             # MODIFIED: extract project_id from path when present
  wrappers/cc.py + codex/cursor/continue_dev  # MODIFIED: append /p/<sha1> to base URL
  bench/runner.py                 # NEW: real A/B runner against Anthropic
  bench/tasks_sample.jsonl        # NEW: 10 sample task descriptions
  eval/__init__.py                # NEW
  eval/recall_at_k.py             # NEW: recall@5 harness
  eval/dataset.py                 # NEW: loader for (query, expected_eid) pairs
tests/unit/
  test_cli_project_hash.py
  test_routing_path_based.py
  test_bench_runner.py
  test_eval_recall.py
  test_chaos_recovery.py
docs/eval/
  recall_dataset_template.jsonl   # NEW: 10 seed (query, expected) pairs
  README.md                       # NEW: how to extend + reproduce numbers
```

Modified files:

```
src/spillover/proxy/middleware.py  # path-based project_id extraction
src/spillover/proxy/app.py         # POST /p/{pid}/v1/messages + /p/{pid}/v1/chat/completions routes
src/spillover/cli.py               # _resolve_pid() helper used by stats/query/bench
src/spillover/wrappers/*.py        # ANTHROPIC_BASE_URL=http://127.0.0.1:8787/p/<sha1>
README.md                          # multi-project usage, A/B demo command
```

---

## Phase 0 — CLI project_id hashing

### Task 1: `cli.py` hashes non-hex project_id

**Files:**
- Modify: `src/spillover/cli.py`
- Create: `tests/unit/test_cli_project_hash.py`

- [ ] **Step 1: Add `_resolve_pid()` helper in `cli.py`**

Insert near the top, below imports:

```python
import hashlib
import re

_HEX_ID = re.compile(r"^[0-9a-f]{6,64}$")


def _resolve_pid(raw: str) -> str:
    """Mirror ProjectIdMiddleware: pass hex IDs through; sha1-hash everything else."""
    if _HEX_ID.match(raw):
        return raw
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
```

Wrap every use of `project_id` in `stats`, `query`, and (later) `bench` commands with `_resolve_pid(project_id)`:

```python
@main.command()
@click.argument("project_id")
def stats(project_id: str):
    project_id = _resolve_pid(project_id)
    config = Config.from_env()
    ...
```

Same for `query`.

- [ ] **Step 2: Test**

```python
import struct

from click.testing import CliRunner

from spillover.archive.writer import Turn, archive_raw
from spillover.cli import _resolve_pid, main
from spillover.storage.sqlite import open_project_db


def test_resolve_pid_passthrough_hex():
    assert _resolve_pid("abcdef12") == "abcdef12"


def test_resolve_pid_hashes_arbitrary():
    pid = _resolve_pid("my-cool-project")
    assert len(pid) == 40
    assert pid != "my-cool-project"


def test_stats_finds_db_when_raw_string_given(tmp_path, monkeypatch):
    """Reproduces the smoke bug: writing with sha1(raw), stats given raw must still find it."""
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    raw = "my-cool-project"
    hashed = _resolve_pid(raw)
    # Proxy writes under the hashed id (mirrors middleware behavior)
    db = open_project_db(tmp_path, hashed)
    try:
        archive_raw(
            db,
            Turn(
                project_id=hashed,
                role="user",
                content="hi",
                tool_calls=[],
                code_refs=[],
                token_count=1,
                ts=1,
            ),
        )
    finally:
        db.close()

    runner = CliRunner()
    # User passes the raw string — stats should find the db
    result = runner.invoke(main, ["stats", raw])
    assert result.exit_code == 0
    assert "episodes: 1" in result.output
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_cli_project_hash.py -v
python -m pytest -v -m "not slow"
python -m ruff check src/ tests/
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "fix(cli): hash non-hex project_id in stats/query like middleware does"
```

---

## Phase 1 — Path-based routing

### Task 2: `/p/<project_id>/v1/messages` route

**Files:**
- Modify: `src/spillover/proxy/middleware.py`
- Modify: `src/spillover/proxy/app.py`
- Create: `tests/unit/test_routing_path_based.py`

Path-based routing means the wrapper sets `ANTHROPIC_BASE_URL=http://127.0.0.1:8787/p/<sha1(cwd)>` and Claude Code's HTTP client appends `/v1/messages` to that base, producing `POST /p/<sha1>/v1/messages`. The proxy reads project_id from the path. Header / env var paths stay supported (backwards-compat).

- [ ] **Step 1: Update `middleware.py`**

```python
from __future__ import annotations

import hashlib
import os
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

_HEX_ID = re.compile(r"^[0-9a-f]{6,64}$")
_PATH_PROJECT = re.compile(r"^/p/([0-9a-zA-Z_\-]{1,64})(/.*)?$")
_EXEMPT_PATHS = {"/metrics", "/health", "/"}


def _resolve_project_id(raw: str) -> str:
    if _HEX_ID.match(raw):
        return raw
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class ProjectIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        path_match = _PATH_PROJECT.match(request.url.path)
        raw = None
        if path_match:
            raw = path_match.group(1)
            # Rewrite scope: ASGI scope path is mutable in Starlette
            new_path = path_match.group(2) or "/"
            request.scope["path"] = new_path
            request.scope["raw_path"] = new_path.encode("utf-8")
        else:
            raw = request.headers.get("x-project") or os.environ.get(
                "SPILLOVER_PROJECT_ID"
            )

        if not raw:
            return JSONResponse(
                {
                    "error": (
                        "no project_id resolved. Pass one of: path prefix "
                        "/p/<id>/..., X-Project header, or SPILLOVER_PROJECT_ID env"
                    )
                },
                status_code=400,
            )
        request.state.project_id = _resolve_project_id(raw)
        return await call_next(request)
```

- [ ] **Step 2: Test**

```python
import hashlib

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from spillover.proxy.middleware import ProjectIdMiddleware


@pytest.fixture
def app_client():
    app = FastAPI()
    app.add_middleware(ProjectIdMiddleware)

    @app.post("/v1/messages")
    async def messages(request: Request):
        return JSONResponse({"project_id": request.state.project_id, "path": request.url.path})

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return TestClient(app)


def test_path_based_routing_hex_id(app_client):
    pid = "abcdef1234"
    r = app_client.post(f"/p/{pid}/v1/messages", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["project_id"] == pid


def test_path_based_routing_hashes_non_hex(app_client):
    raw = "my-project"
    r = app_client.post(f"/p/{raw}/v1/messages", json={})
    assert r.status_code == 200
    expected = hashlib.sha1(raw.encode()).hexdigest()
    assert r.json()["project_id"] == expected


def test_header_still_works(app_client):
    r = app_client.post("/v1/messages", json={}, headers={"X-Project": "abcdef12"})
    assert r.status_code == 200
    assert r.json()["project_id"] == "abcdef12"


def test_no_project_anywhere_returns_400(app_client, monkeypatch):
    monkeypatch.delenv("SPILLOVER_PROJECT_ID", raising=False)
    r = app_client.post("/v1/messages", json={})
    assert r.status_code == 400


def test_health_exempt_from_path_check(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
```

- [ ] **Step 3: Run**

```
python -m pytest tests/unit/test_routing_path_based.py -v
```

- [ ] **Step 4: Update wrappers** to use path-based routing

In each of `wrappers/cc.py`, `codex.py`, `cursor.py`, `continue_dev.py`, change:

```python
proxy_url = proxy or f"http://127.0.0.1:{config.port}"
```

to:

```python
proxy_url = proxy or f"http://127.0.0.1:{config.port}/p/{project_id}"
```

The CLI appends `/v1/messages` to its base, so the final URL is `/p/<id>/v1/messages` — matched by the new middleware path regex.

Update the wrapper tests in `tests/unit/test_wrapper_cc.py` and `test_wrappers_extra.py` to assert the new URL shape.

- [ ] **Step 5: Run + commit**

```
python -m pytest -v -m "not slow"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(proxy): path-based /p/<id>/v1/... routing; wrappers use per-project base"
```

---

## Phase 2 — Bench runner real

### Task 3: `bench/runner.py` — A/B against Anthropic

**Files:**
- Create: `src/spillover/bench/runner.py`
- Create: `src/spillover/bench/tasks_sample.jsonl`
- Modify: `src/spillover/cli.py` (add `--run` flag to `bench`)
- Create: `tests/unit/test_bench_runner.py`

The runner takes a JSONL file of tasks. For each task, it builds a multi-turn conversation that represents real work the agent did (in the form of `messages: [...]` arrays), then asks a closing question that requires *remembering* the earlier turns.

Vanilla mode: send the conversation to Anthropic directly (or via the proxy with retrieval disabled).
spillover mode: arrange the conversation so eviction triggers, then send only the closing question and rely on LTM injection.

The output: per-task, did the response include the expected anchor strings? Aggregate over N tasks.

- [ ] **Step 1: Write `bench/tasks_sample.jsonl`**

```
{"id": "task01", "history": [{"role": "user", "content": "we picked SQLite over Postgres because the deployment is local-only"}, {"role": "assistant", "content": "noted: SQLite for local-only deployment"}], "question": "remind me which database we chose and why", "expected_anchors": ["SQLite", "local"]}
{"id": "task02", "history": [{"role": "user", "content": "the auth bug was in middleware.py line 42 — jwt expiry uses < instead of <="}, {"role": "assistant", "content": "confirmed: middleware.py:42 jwt expiry check"}], "question": "where was the auth bug?", "expected_anchors": ["middleware", "42", "jwt"]}
{"id": "task03", "history": [{"role": "user", "content": "ADR-014 captures the decision to drop the legacy v1 auth middleware"}, {"role": "assistant", "content": "ADR-014 recorded"}], "question": "what does ADR-014 say?", "expected_anchors": ["legacy", "auth"]}
{"id": "task04", "history": [{"role": "user", "content": "deploy uses Coolify with letsencryptresolver and traefik labels hardcoded — no ${} interpolation"}, {"role": "assistant", "content": "deploy pattern noted"}], "question": "what's our coolify deploy pattern?", "expected_anchors": ["letsencryptresolver", "traefik"]}
{"id": "task05", "history": [{"role": "user", "content": "erica is my wife, T1 diabetes since 2018, uses Basaglar + Fiasp pens"}, {"role": "assistant", "content": "got it"}], "question": "tell me about Erica's diabetes regimen", "expected_anchors": ["Basaglar", "Fiasp"]}
```

- [ ] **Step 2: `src/spillover/bench/runner.py`**

```python
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass
class TaskResult:
    task_id: str
    mode: str  # "vanilla" | "spillover"
    response_text: str
    input_tokens: int
    output_tokens: int
    anchors_hit: list[str] = field(default_factory=list)
    anchors_missed: list[str] = field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None


def _call_anthropic(
    base_url: str,
    auth: str,
    payload: dict,
    extra_headers: dict | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict]:
    headers = {
        "Authorization": auth,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{base_url}/v1/messages", json=payload, headers=headers)
        r.raise_for_status()
        return r.status_code, r.json()


def _extract_text(resp: dict) -> str:
    return "".join(
        b.get("text", "")
        for b in resp.get("content", [])
        if isinstance(b, dict)
    )


def _check_anchors(text: str, anchors: list[str]) -> tuple[list[str], list[str]]:
    hits = [a for a in anchors if a.lower() in text.lower()]
    misses = [a for a in anchors if a not in hits]
    return hits, misses


def run_task_vanilla(
    task: dict,
    base_url: str,
    auth: str,
    model: str = "claude-haiku-4-5-20251001",
) -> TaskResult:
    """Vanilla = send history + question as one conversation, no LTM injection."""
    messages = list(task["history"]) + [{"role": "user", "content": task["question"]}]
    payload = {
        "model": model,
        "max_tokens": 200,
        "messages": messages,
    }
    t0 = time.time()
    try:
        _, resp = _call_anthropic(base_url, auth, payload)
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check_anchors(text, task["expected_anchors"])
        return TaskResult(
            task_id=task["id"],
            mode="vanilla",
            response_text=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            anchors_hit=hits,
            anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return TaskResult(
            task_id=task["id"],
            mode="vanilla",
            response_text="",
            input_tokens=0,
            output_tokens=0,
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def run_task_spillover(
    task: dict,
    proxy_base_url: str,  # http://127.0.0.1:8787/p/<sha1>
    auth: str,
    model: str = "claude-haiku-4-5-20251001",
    seed_first: bool = True,
) -> TaskResult:
    """spillover mode: seed history in a first call (force eviction), then ask question alone.

    Tests whether LTM retrieval restores the answer despite the question being sent
    without the history.
    """
    if seed_first:
        seed_messages = list(task["history"]) + [
            {"role": "user", "content": "(internal: seed turn for retriever)"}
        ]
        try:
            _call_anthropic(
                proxy_base_url,
                auth,
                {
                    "model": model,
                    "max_tokens": 30,
                    "messages": seed_messages,
                },
                extra_headers={"anthropic-beta": "oauth-2025-04-20"},
            )
        except Exception:
            pass
        # Give facet pipeline a beat
        time.sleep(1.5)

    payload = {
        "model": model,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": task["question"]}],
    }
    t0 = time.time()
    try:
        _, resp = _call_anthropic(
            proxy_base_url,
            auth,
            payload,
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check_anchors(text, task["expected_anchors"])
        return TaskResult(
            task_id=task["id"],
            mode="spillover",
            response_text=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            anchors_hit=hits,
            anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return TaskResult(
            task_id=task["id"],
            mode="spillover",
            response_text="",
            input_tokens=0,
            output_tokens=0,
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def run_ab(
    tasks_path: Path,
    auth: str,
    proxy_base_url: str,
    vanilla_base_url: str = "https://api.anthropic.com",
    model: str = "claude-haiku-4-5-20251001",
) -> list[TaskResult]:
    results: list[TaskResult] = []
    with tasks_path.open(encoding="utf-8") as f:
        tasks = [json.loads(line) for line in f if line.strip()]
    for task in tasks:
        results.append(run_task_vanilla(task, vanilla_base_url, auth, model=model))
        results.append(run_task_spillover(task, proxy_base_url, auth, model=model))
    return results


def render_ab_report(results: list[TaskResult]) -> str:
    by_mode = {"vanilla": [], "spillover": []}
    for r in results:
        by_mode[r.mode].append(r)
    lines = ["# spillover A/B benchmark", "", "## summary", "", "| metric | vanilla | spillover |", "|---|---:|---:|"]
    for mode in ("vanilla", "spillover"):
        if not by_mode[mode]:
            continue
    v = by_mode["vanilla"]
    s = by_mode["spillover"]

    def _ratio(rs: list[TaskResult]) -> str:
        if not rs:
            return "-"
        full = sum(1 for r in rs if r.anchors_missed == [])
        return f"{full}/{len(rs)}"

    def _toks(rs: list[TaskResult], field_: str) -> int:
        return sum(getattr(r, field_) for r in rs)

    lines.append(f"| tasks with all anchors hit | {_ratio(v)} | {_ratio(s)} |")
    lines.append(f"| total input_tokens | {_toks(v, 'input_tokens')} | {_toks(s, 'input_tokens')} |")
    lines.append(f"| total output_tokens | {_toks(v, 'output_tokens')} | {_toks(s, 'output_tokens')} |")
    lines.append(f"| total errors | {sum(1 for r in v if r.error)} | {sum(1 for r in s if r.error)} |")

    lines.append("\n## per-task\n")
    lines.append("| task | mode | hits | misses | input | output | latency_ms |")
    lines.append("|---|---|---|---|---:|---:|---:|")
    for r in results:
        hits = ",".join(r.anchors_hit) or "-"
        misses = ",".join(r.anchors_missed) or "-"
        lines.append(
            f"| {r.task_id} | {r.mode} | {hits} | {misses} | {r.input_tokens} | {r.output_tokens} | {r.latency_ms} |"
        )
    return "\n".join(lines) + "\n"


def main_offline_demo(tasks_path: Path, results_path: Path) -> None:
    """Read pre-scored results, render markdown — same as Plan 4 bench/ab.py."""
    raw = json.loads(tasks_path.read_text(encoding="utf-8"))
    results = [TaskResult(**r) for r in raw]
    results_path.write_text(render_ab_report(results), encoding="utf-8")
```

- [ ] **Step 3: Extend `cli.py` bench command**

```python
@main.command()
@click.option("--tasks", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--report", type=click.Path(dir_okay=False), default="bench-report.md")
@click.option("--run", is_flag=True, default=False,
              help="Actually run the benchmark against Anthropic (requires OAuth in ~/.claude/.credentials.json or ANTHROPIC_API_KEY)")
@click.option("--proxy-url", default="http://127.0.0.1:8787")
@click.option("--vanilla-url", default="https://api.anthropic.com")
@click.option("--project", default=None, help="Override project_id for spillover runs (default: random per session)")
@click.option("--model", default="claude-haiku-4-5-20251001")
def bench(tasks: str, report: str, run: bool, proxy_url: str, vanilla_url: str,
          project: str | None, model: str):
    """Run the offline A/B harness OR render a markdown report from a scoring file."""
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
    import hashlib
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
        click.echo("No auth available. Set ANTHROPIC_API_KEY or run `claude` once to populate OAuth.", err=True)
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
```

(Add `from dataclasses import asdict` and `from pathlib import Path` imports at the top of `cli.py` if not already there.)

- [ ] **Step 4: Test**

```python
import json

from spillover.bench.runner import (
    TaskResult,
    _check_anchors,
    _extract_text,
    render_ab_report,
)


def test_check_anchors_case_insensitive():
    hits, misses = _check_anchors("the SQLite db is local", ["sqlite", "local", "postgres"])
    assert "sqlite" in hits
    assert "local" in hits
    assert "postgres" in misses


def test_extract_text_anthropic_shape():
    text = _extract_text({"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": " world"}]})
    assert text == "hello world"


def test_render_ab_report_has_per_task_rows():
    results = [
        TaskResult(
            task_id="t1", mode="vanilla", response_text="ok",
            input_tokens=10, output_tokens=5,
            anchors_hit=["foo"], anchors_missed=[]
        ),
        TaskResult(
            task_id="t1", mode="spillover", response_text="ok",
            input_tokens=12, output_tokens=5,
            anchors_hit=["foo"], anchors_missed=[]
        ),
    ]
    md = render_ab_report(results)
    assert "| t1 | vanilla |" in md
    assert "| t1 | spillover |" in md
    assert "tasks with all anchors hit" in md
```

- [ ] **Step 5: Run + commit**

```
python -m pytest tests/unit/test_bench_runner.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(bench): real A/B runner against Anthropic + spillover bench --run flag"
```

---

## Phase 3 — Recall@5 harness

### Task 4: `eval/recall_at_k.py` + dataset template

**Files:**
- Create: `src/spillover/eval/__init__.py` (empty)
- Create: `src/spillover/eval/dataset.py`
- Create: `src/spillover/eval/recall_at_k.py`
- Create: `docs/eval/recall_dataset_template.jsonl`
- Create: `docs/eval/README.md`
- Create: `tests/unit/test_eval_recall.py`

- [ ] **Step 1: `eval/dataset.py`**

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvalPair:
    query: str
    expected_episode_id: str
    notes: str = ""


def load_pairs(path: Path) -> list[EvalPair]:
    out: list[EvalPair] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            obj = json.loads(line)
            out.append(
                EvalPair(
                    query=obj["query"],
                    expected_episode_id=obj["expected_episode_id"],
                    notes=obj.get("notes", ""),
                )
            )
    return out
```

- [ ] **Step 2: `eval/recall_at_k.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from spillover.eval.dataset import EvalPair, load_pairs
from spillover.facet.embed import embed_text
from spillover.facet.entities import extract_entities
from spillover.retriever.fusion import rrf_fuse
from spillover.retriever.graph import graph_walk
from spillover.retriever.vector import vector_topk
from spillover.storage.kuzu import open_project_kuzu
from spillover.storage.sqlite import open_project_db


@dataclass
class RecallResult:
    pair: EvalPair
    rank: int | None  # 1-indexed rank of expected_episode_id in fused hits; None if not found in top-K
    top_ids: list[str] = None  # type: ignore[assignment]


def evaluate_recall(
    db_root: Path,
    project_id: str,
    pairs: list[EvalPair],
    *,
    vector_k: int = 50,
    graph_k: int = 50,
    final_k: int = 20,
) -> list[RecallResult]:
    db = open_project_db(db_root, project_id)
    try:
        results: list[RecallResult] = []
        for pair in pairs:
            emb = embed_text(pair.query)
            v_hits = vector_topk(db, emb, k=vector_k)
            seeds = [e.name for e in extract_entities(pair.query)][:20]
            g_hits: list = []
            if seeds:
                try:
                    kuzu_conn = open_project_kuzu(db_root, project_id)
                    g_hits = graph_walk(kuzu_conn, seeds, k_hop=2, limit=graph_k)
                except Exception:
                    pass
            fused = rrf_fuse(v_hits, g_hits)[:final_k]
            top_ids = [h.episode_id for h in fused]
            rank: int | None = None
            if pair.expected_episode_id in top_ids:
                rank = top_ids.index(pair.expected_episode_id) + 1
            results.append(RecallResult(pair=pair, rank=rank, top_ids=top_ids))
        return results
    finally:
        db.close()


def recall_at_k(results: list[RecallResult], k: int) -> float:
    if not results:
        return 0.0
    hits = sum(1 for r in results if r.rank is not None and r.rank <= k)
    return hits / len(results)


def render_recall_report(results: list[RecallResult]) -> str:
    lines = ["# Recall@K report", ""]
    for k in (1, 3, 5, 10, 20):
        r = recall_at_k(results, k)
        lines.append(f"- recall@{k}: **{r * 100:.1f}%** ({sum(1 for x in results if x.rank is not None and x.rank <= k)}/{len(results)})")
    lines.append("\n## misses\n")
    for r in results:
        if r.rank is None:
            lines.append(f"- query=`{r.pair.query}` expected=`{r.pair.expected_episode_id}` — not in top-{len(r.top_ids)}")
    return "\n".join(lines) + "\n"


def load_and_evaluate(
    db_root: Path,
    project_id: str,
    dataset_path: Path,
) -> str:
    pairs = load_pairs(dataset_path)
    results = evaluate_recall(db_root, project_id, pairs)
    return render_recall_report(results)
```

- [ ] **Step 3: `docs/eval/recall_dataset_template.jsonl`**

```
{"query": "where did we decide to use SQLite over Postgres", "expected_episode_id": "REPLACE_ME_WITH_REAL_EPISODE_ID", "notes": "decision turn"}
{"query": "what was the auth bug at middleware.py:42", "expected_episode_id": "REPLACE_ME_WITH_REAL_EPISODE_ID", "notes": "diagnosed jwt expiry comparison"}
{"query": "what does ADR-014 cover", "expected_episode_id": "REPLACE_ME_WITH_REAL_EPISODE_ID"}
{"query": "what was the coolify deploy pattern with traefik", "expected_episode_id": "REPLACE_ME_WITH_REAL_EPISODE_ID"}
{"query": "what is erica's insulin regimen", "expected_episode_id": "REPLACE_ME_WITH_REAL_EPISODE_ID"}
{"query": "qual era o bug do middleware auth", "expected_episode_id": "REPLACE_ME_WITH_REAL_EPISODE_ID", "notes": "PT-BR query, same content"}
```

- [ ] **Step 4: `docs/eval/README.md`**

```markdown
# Evaluation

This directory holds the reproducible recall benchmark for spillover's hybrid retriever.

## Build your own dataset

1. Run `spillover stats <project_id>` to find a project DB with > 100 episodes.
2. Run `spillover query <project_id> "<sample query>"` and pick a known-correct hit.
3. Record its `episode_id` and the query that should retrieve it.
4. Repeat for 50+ queries covering coding, decisions, bug fixes, conversational facts.
5. Save lines as `dataset.jsonl` with shape:

```json
{"query": "...", "expected_episode_id": "<uuid>", "notes": "..."}
```

## Run

```bash
python -c "from spillover.eval.recall_at_k import load_and_evaluate; from pathlib import Path; print(load_and_evaluate(Path('~/.spillover').expanduser(), '<project_id>', Path('docs/eval/dataset.jsonl')))"
```

## Targets

- recall@1: > 60%
- recall@5: > 90%  (spec acceptance gate)
- recall@10: > 95%
```

- [ ] **Step 5: Tests**

```python
import struct

from spillover.archive.writer import Turn, archive_raw
from spillover.eval.dataset import EvalPair
from spillover.eval.recall_at_k import (
    RecallResult,
    evaluate_recall,
    recall_at_k,
    render_recall_report,
)
from spillover.storage.sqlite import open_project_db


def _seed_episode(tmp_path, content):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content=content,
                tool_calls=[],
                code_refs=[],
                token_count=10,
                ts=1,
            ),
        )
        # Insert vec row with placeholder zero embedding
        vec = [0.0] * 768
        db.execute(
            "INSERT INTO vec_episodes(episode_id, embedding, memory_type, importance, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, struct.pack("<768f", *vec), "episodic", 1.0, 1),
        )
    finally:
        db.close()
    return eid


def test_recall_at_k_counts_hits():
    results = [
        RecallResult(pair=EvalPair("q1", "ep1"), rank=1, top_ids=["ep1"]),
        RecallResult(pair=EvalPair("q2", "ep2"), rank=4, top_ids=["a", "b", "c", "ep2"]),
        RecallResult(pair=EvalPair("q3", "ep3"), rank=None, top_ids=["x", "y", "z"]),
    ]
    assert recall_at_k(results, 1) == pytest.approx(1 / 3)
    assert recall_at_k(results, 5) == pytest.approx(2 / 3)
    assert recall_at_k(results, 10) == pytest.approx(2 / 3)


def test_render_recall_report_includes_misses():
    results = [
        RecallResult(pair=EvalPair("q1", "ep1"), rank=None, top_ids=["x", "y"]),
    ]
    md = render_recall_report(results)
    assert "recall@5" in md
    assert "misses" in md.lower()
    assert "ep1" in md
```

(Add `import pytest` at top of test file.)

- [ ] **Step 6: Run + commit**

```
python -m pytest tests/unit/test_eval_recall.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(eval): recall@k harness + dataset template + docs"
```

---

## Phase 4 — Chaos test

### Task 5: SIGKILL recovery test

**Files:**
- Create: `tests/integration/test_chaos_recovery.py`

The chaos test is intentionally simulated, not literal SIGKILL — pytest can't reliably kill the in-process FastAPI test client. We approximate it by:
1. Archiving N turns to the project DB.
2. Hard-closing the DB handle mid-loop (mimicking SIGKILL between `archive_raw` calls).
3. Re-opening the DB and asserting the partial set is intact and queryable.
4. Re-running facet pipeline on the survivors.

For a literal SIGKILL test, see `docs/eval/README.md` for the manual procedure.

- [ ] **Step 1: Write the test**

```python
import time

from spillover.archive.writer import Turn, archive_raw
from spillover.facet.worker import FacetEvent, _process_one
from spillover.storage.sqlite import open_project_db


def test_archive_durability_after_handle_drop(tmp_path):
    """Simulates SIGKILL mid-archive: some episodes already written, some not.
    On 'restart' (reopening db), the persisted set must be intact."""
    db = open_project_db(tmp_path, "p1")
    try:
        eid_a = archive_raw(
            db,
            Turn(project_id="p1", role="user", content="A",
                 tool_calls=[], code_refs=[], token_count=1, ts=1),
        )
        eid_b = archive_raw(
            db,
            Turn(project_id="p1", role="user", content="B",
                 tool_calls=[], code_refs=[], token_count=1, ts=2),
        )
    finally:
        db.close()  # simulate crash before episodes C, D, E

    # "Restart"
    db = open_project_db(tmp_path, "p1")
    try:
        rows = db.execute(
            "SELECT id, content_json, facet_pending FROM episodes ORDER BY ts"
        ).fetchall()
        assert len(rows) == 2
        assert {r["id"] for r in rows} == {eid_a, eid_b}
        # All survivors should still be facet_pending=1 (worker didn't run pre-crash)
        assert all(r["facet_pending"] == 1 for r in rows)
    finally:
        db.close()


@pytest.mark.slow
def test_facet_pipeline_can_replay_survivors(tmp_path):
    """After 'crash', the facet worker can pick up pending episodes and process them."""
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(project_id="p1", role="user",
                 content="auth bug at middleware.py:42",
                 tool_calls=[], code_refs=[], token_count=10, ts=1),
        )
    finally:
        db.close()
    _process_one(FacetEvent(project_id="p1", episode_id=eid, db_root=tmp_path))
    db = open_project_db(tmp_path, "p1")
    try:
        row = db.execute(
            "SELECT facet_pending, memory_type FROM episodes WHERE id=?", (eid,)
        ).fetchone()
        assert row["facet_pending"] == 0
        assert row["memory_type"] is not None
        vec_row = db.execute(
            "SELECT episode_id FROM vec_episodes WHERE episode_id=?", (eid,)
        ).fetchone()
        assert vec_row is not None
    finally:
        db.close()


def test_seen_turns_survives_restart(tmp_path):
    """seen_turns table durability: counter-compaction defense survives crash."""
    from spillover.counter_compact.detection import record_seen_turns
    db = open_project_db(tmp_path, "p1")
    try:
        record_seen_turns(db, "p1", [
            {"role": "assistant", "content": "this must survive crash"},
        ])
    finally:
        db.close()
    db = open_project_db(tmp_path, "p1")
    try:
        rows = db.execute(
            "SELECT content_json FROM seen_turns WHERE project_id=?", ("p1",)
        ).fetchall()
        assert len(rows) == 1
        assert "must survive" in rows[0]["content_json"]
    finally:
        db.close()
```

(Add `import pytest` at top.)

- [ ] **Step 2: Run + commit**

```
python -m pytest tests/integration/test_chaos_recovery.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "test(chaos): durability simulation (handle-drop survives, seen_turns persists, facet replays)"
```

---

## Phase 5 — README A/B section + verify + tag v1.3.0 + push

### Task 6: README — add "Run the A/B demo" section

**Files:**
- Modify: `README.md`

Insert a new section between "Does it actually work?" and "What is in the box":

```markdown
## Run the A/B demo yourself

After installing, run a side-by-side comparison against your real Anthropic
account. Each task is a multi-turn conversation followed by a question that
requires *remembering* the earlier turns.

```bash
# 1. start the proxy
spillover up &

# 2. run A/B (uses OAuth from ~/.claude/.credentials.json if no ANTHROPIC_API_KEY)
spillover bench \
  --tasks src/spillover/bench/tasks_sample.jsonl \
  --report bench-report.md \
  --run

cat bench-report.md
```

You will see two rows per task: `vanilla` (history sent inline) and `spillover` (only the question sent — history must be recalled via LTM). The report counts:

- how many tasks each mode answered with all the expected anchor strings present
- total tokens spent on each mode
- per-task latency
```

- [ ] **Step 1: Commit README change**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "docs(readme): add A/B demo command + per-task interpretation"
```

### Task 7: Full suite + tag + push

- [ ] **Step 1: Full suite**

```
python -m pytest -v -m "not slow"
python -m pytest -v
python -m ruff check src/ tests/
```

Expected: ~195 fast PASSED, ~202 with slow. Ruff clean.

- [ ] **Step 2: Tag + push**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit --allow-empty -m "chore: spillover v1.3.0 (Plan 7 done - eval + routing + chaos)"
git tag -a v1.3.0 -m "spillover v1.3.0 - eval harness + path-based routing + chaos test"
git push -u origin feat/plan7-eval-and-routing
git push origin --tags
```

- [ ] **Step 3: Merge to master + push**

```
git checkout master
git merge --no-ff feat/plan7-eval-and-routing -m "Merge Plan 7: eval + multi-project routing (v1.3.0)"
git push origin master
git push origin --tags
```

---

## Definition of Done

1. Plan 1–6 tests still pass; new tests add ~25.
2. `ruff check src/ tests/` exits 0.
3. `spillover stats <raw-string>` resolves the same hashed id the proxy writes — smoke bug closed.
4. `POST /p/<id>/v1/messages` route accepts a project_id from the URL prefix.
5. Wrappers (`spillover-cc`, etc.) set `ANTHROPIC_BASE_URL=http://127.0.0.1:8787/p/<sha1>`; multi-project works without restarting the daemon.
6. `spillover bench --run --tasks ...` runs A/B against Anthropic and emits a per-task markdown report with anchor-hit counts.
7. `eval/recall_at_k.py` measures recall@k against a JSONL dataset.
8. Chaos test passes: handle-drop survivors intact, facet worker replays pending, seen_turns survives.
9. README gained the "Run the A/B demo" section.
10. `v1.3.0` tag exists locally + pushed; both branches pushed to `origin`.

End of plan.
