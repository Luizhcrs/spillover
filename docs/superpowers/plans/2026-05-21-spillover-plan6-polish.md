# spillover Plan 6: Polish + README Normalize

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining Important issues from the v1.0.0 code review (I3 batch SELECT, I4 httpx retry, I7 redaction, M9 prune scheduling) and rewrite the README so it documents the actual v1.1.0 product, not the v0.1 bootstrap stub.

**Tech stack:** no new deps.

End state: v1.2.0 tagged, README accurate, perf/observability tighter, 180+ tests passing.

---

## Phase 0 — README normalize

### Task 1: Rewrite `README.md` for v1.1.0 reality

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace `README.md` entirely with this content**

```markdown
# spillover

Transparent LLM proxy with overflow memory architecture.

> Agents never compact. They spill over.

**Status:** v1.1.0 — operational. Closed source. Private.

---

## What spillover does

spillover sits between any Anthropic / OpenAI client (Claude Code, Codex,
Cursor, Continue.dev, raw SDK scripts) and the upstream provider. It does
three things the provider does not:

1. **Externalises old turns as raw episodes** when the active context crosses
   a soft-ceiling watermark. The agent's context stays near max capacity —
   never compacted, never summarised. Token-balanced 1:1 — N tokens in equals
   N oldest tokens out.

2. **Injects relevant past episodes back as long-term memory** via hybrid
   retrieval (vector top-K from `sqlite-vec` + k-hop graph walk from
   `Kuzu`, fused with Reciprocal Rank Fusion). The agent reads its own
   prior decisions and tool calls as part of every new prompt — without
   knowing the proxy exists.

3. **Defends against client-side compaction.** Most CLIs auto-compact when
   they perceive context pressure. spillover rewrites the `usage.input_tokens`
   it returns so the client believes it has headroom, intercepts explicit
   compaction requests, and rescues turns the client drops anyway by diffing
   against a `seen_turns` table.

The architectural opposition is explicit. spillover is the inverse of every
prompt-compression scheme: instead of compressing the conversation into a
smaller representation, it externalises it intact and retrieves on demand.

---

## Install

```bash
git clone https://github.com/Luizhcrs/spillover
cd spillover
pip install -e ".[dev]"
```

Python 3.11+ required. First retrieval call downloads the embedding model
(`nomic-ai/nomic-embed-text-v1.5-Q`, ~130 MB) into the local `fastembed`
cache.

---

## Run

Start the proxy:

```bash
spillover up
```

Default listens on `http://127.0.0.1:8787`. Forwards to
`https://api.anthropic.com` (`/v1/messages`) and `https://api.openai.com`
(`/v1/chat/completions`).

Point any client at it:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 \
SPILLOVER_PROJECT_ID=$(pwd | sha1sum | cut -c1-40) \
claude code
```

Or use one of the bundled wrappers:

```bash
spillover-cc       # Claude Code
spillover-codex    # Codex
spillover-cursor   # Cursor
spillover-continue # Continue.dev
```

Each wrapper sets `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL`, the disable-compact
env vars known for that CLI, and a per-project `SPILLOVER_PROJECT_ID` derived
from the current working directory.

---

## Configuration

All settings come from env vars; sensible defaults shipped.

| Variable | Default | Notes |
|----------|---------|-------|
| `SPILLOVER_PORT` | `8787` | proxy listen port |
| `SPILLOVER_OPERATIONAL_CEILING_TOKENS` | `200000` | soft ceiling; eviction math uses this |
| `SPILLOVER_PROVIDER_MAX_TOKENS` | `2*ceiling` | informational (real provider window) |
| `SPILLOVER_WATERMARK` | `0.85` | fraction of ceiling that triggers eviction |
| `SPILLOVER_DB_ROOT` | `~/.spillover` | per-project SQLite + Kuzu live here |
| `SPILLOVER_UPSTREAM_BASE_URL` | `https://api.anthropic.com` | |
| `SPILLOVER_OPENAI_BASE_URL` | `https://api.openai.com` | |
| `SPILLOVER_SYSTEM_PCT` | `0.04` | budget split |
| `SPILLOVER_WORKING_MEMORY_PCT` | `0.20` | recent turns reserve |
| `SPILLOVER_ACTIVE_PCT` | `0.50` | bulk conversation |
| `SPILLOVER_LTM_BUDGET_PCT` | `0.15` | LTM injection cap |
| `SPILLOVER_SCRATCHPAD_PCT` | `0.11` | response reserve |
| `SPILLOVER_PROFILE_DEFAULT` | `auto` | `auto`/`coding`/`research`/`conversation` |
| `SPILLOVER_RETRIEVER_TOPK` | `8` | fused hits after RRF |
| `SPILLOVER_RETRIEVER_VECTOR_K` | `50` | vector candidates before fusion |
| `SPILLOVER_RETRIEVER_GRAPH_K` | `50` | graph candidates before fusion |
| `SPILLOVER_STREAM_REWRITE` | `1` | `0` to disable streaming usage rewrite |
| `SPILLOVER_LOG_LEVEL` | `INFO` | |
| `SPILLOVER_PROJECT_ID` | (none) | fallback when no `X-Project` header sent |

Budget tiers must sum to 1.0. Soft ceiling can be set far below the provider
window (e.g. 500k of 1M on Opus) to reserve a safety buffer and dodge
attention degradation in the middle of the context.

---

## Commands

```bash
spillover up                              # start proxy
spillover stats <project_id>              # episodes / evicted / pinned / embedded / facet_pending
spillover query <project_id> "<text>"     # ad-hoc hybrid retrieval, prints ranked hits
spillover bench --tasks scoring.json      # render markdown A/B report

spillover-cc                              # launch Claude Code wired in
spillover-codex
spillover-cursor
spillover-continue
```

---

## Architecture

```
                    +---------------------+
   CLI (CC/Codex/   |  spillover-wrapper  |  inject SPILLOVER_PROJECT_ID env
   Cursor/Continue) +----------+----------+  + ANTHROPIC_BASE_URL / OPENAI_BASE_URL
                               | HTTP
                               v
                    +---------------------+
                    |   spillover proxy   |  FastAPI + asyncio
                    |   :8787             |
                    +---------------------+
                       |        ^      |
                  pre  |        |      | post
                       v        |      v
        +--------------+   +----+---+   +---------------+
        | retriever    |   | adapt. |   | overflow      |
        | (RRF fusion) |   | anth/  |   | trigger +     |
        +------+-------+   | openai |   | archiver      |
               |           +---+----+   +-------+-------+
               v               |                v
        +-----------+          v        +---------------+
        | sqlite-vec|   provider real   | facet queue   |
        | + Kuzu    |   (Anthropic/     | (asyncio)     |
        | (per proj)|    OpenAI)        +-------+-------+
        +-----------+                           |
               ^                                v
               +-------------- facet extractor (embed, NER, decisions, classifier)
```

Components, each one job:

- `adapters/` — translate Anthropic Messages and OpenAI Chat Completions
  payloads ↔ internal `Conversation` dataclass.
- `archive/` — `archive_raw(turn)` writes one row to `episodes` with sha256
  dedup; `UNIQUE(hash)` plus `IntegrityError` retry makes it race-safe.
- `eviction/` — char/4 heuristic tokenizer + 3-pass weighted-FIFO selector
  (FIFO non-priority → priority fallback → budget pressure). Density = number
  of structured signals on the turn (tool calls, entities); high-density
  turns evicted last.
- `storage/` — per-project SQLite (with `sqlite-vec` loaded for the
  `vec_episodes` virtual table) + per-project Kuzu graph DB (cached LRU 32,
  schema initialised once per process).
- `facet/` — async worker pulling `FacetEvent` from an `asyncio.Queue`
  (`maxsize=1024`, dropped events counted). For each event: embed via
  fastembed, extract entities (regex), decisions (PT-BR + EN), code refs;
  classify type (`priority`/`procedural`/`semantic`/`episodic`); write to
  `vec_episodes` and graph.
- `retriever/` — vector top-K + graph k-hop, fused via RRF with type weights
  (priority 1.5, procedural 1.2, others 1.0), trimmed to LTM token budget,
  rendered as `<spillover-ltm>` block.
- `counter_compact/` — usage rewrite, compact-request intercept, conversation
  diff rescue (`seen_turns` table), incremental SSE usage rewrite.
- `decay/` — every 6h, recompute importance as
  `base * exp(-age/half_life) + min(hit_count*0.05, 0.5)`. Half-lives:
  procedural 30d, semantic 14d, episodic 7d, priority 60d. Pinned skip.
- `metrics/` — Prometheus counters and gauges; exposed at `GET /metrics`.
- `wrappers/` — Click entry-points for each supported CLI.
- `bench/` — offline A/B harness, markdown report.

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/messages` | Anthropic Messages passthrough + LTM + eviction + defences |
| `POST` | `/v1/chat/completions` | OpenAI Chat Completions, same pipeline |
| `GET` | `/metrics` | Prometheus text exposition |
| `GET` | `/health` | liveness (200) |

The first two require either an `X-Project` HTTP header or a
`SPILLOVER_PROJECT_ID` env var on the proxy process. The wrappers set the env
var. `/metrics`, `/health`, and `/` are exempt.

---

## Status of the v1 product

| Capability | State |
|------------|-------|
| Anthropic non-streaming + streaming passthrough | done |
| OpenAI non-streaming + streaming passthrough | done |
| Per-project SQLite + Kuzu + sqlite-vec | done |
| 3-pass weighted-FIFO eviction with token-balance invariant | done |
| Hybrid retriever (vector + graph + RRF + budget + render) | done |
| Async facet pipeline with fastembed + regex NER + classifier | done |
| Counter-compaction: usage rewrite (non-streaming + incremental SSE) | done |
| Counter-compaction: intercept + `seen_turns` rescue | done |
| Soft-ceiling 5-tier budget + dynamic profile | done |
| Prometheus metrics wired across hot path | done |
| Decay scheduler with pinned exemption | done |
| 4 CLI wrappers (`cc` / `codex` / `cursor` / `continue`) | done |
| Offline A/B benchmark harness | partial — markdown render only, runner stubbed |
| AST snapshot diffs of touched files | not started (v2 candidate) |
| Recall@5 evaluation harness | not started |
| Chaos test (kill mid-archive) | not started |

---

## Roadmap

- **v1.2 / Plan 6** — batch SELECT in retriever + render + decay (kill N+1),
  httpx retry + backoff, redact `Authorization` from logs, prune scheduled
  for `seen_turns`, real bench runner.
- **v1.3 / Plan 7 candidates** — HyDE query expansion, BM25 lexical leg,
  Self-RAG retrieval gate, ColBERT late-interaction reranker.
- **v2 / Plan 8 candidates** — AST snapshot per touched file via treesitter,
  cross-project pool with opt-in tag, multi-tenant tenant_id schema.

---

## Design references

- `docs/superpowers/specs/2026-05-20-spillover-design.md` — full design spec.
- `docs/superpowers/plans/2026-05-20-spillover-mvp-foundation.md` — Plan 1.
- `docs/superpowers/plans/2026-05-21-spillover-retriever.md` — Plan 2.
- `docs/superpowers/plans/2026-05-21-spillover-counter-compaction.md` — Plan 3.
- `docs/superpowers/plans/2026-05-21-spillover-multi-cli-polish.md` — Plan 4.
- `docs/superpowers/plans/2026-05-21-spillover-plan5-soft-ceiling-and-fixes.md` — Plan 5.

---

## Papers spillover engages

- Liu et al. 2024, "Lost in the Middle" (TACL) — directly addresses by
  injecting LTM at high-attention positions.
- Packer et al. 2023, "MemGPT: LLMs as Operating Systems" — same paging
  philosophy without requiring the agent to call tools.
- Gan & Sun 2025, "RAG-MCP" — same retrieval-precision insight applied to
  cognitive episodes instead of tools.
- Cormack et al. 2009, "Reciprocal Rank Fusion" (SIGIR) — used directly in
  the retriever.
- Anti-position vs Rae et al. 2019 ("Compressive Transformer"), Jiang et al.
  2023 ("LongLLMLingua"), Ge et al. 2023 ("ICAE"): spillover preserves raw
  by design.

---

## License

Proprietary. All rights reserved.
```

- [ ] **Step 2: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "docs(readme): rewrite for v1.1.0 reality (features, config, architecture, status, papers)"
```

---

## Phase 1 — I3: batch SELECT (kill N+1)

### Task 2: Replace per-hit SELECTs with batch IN-clauses

**Files:**
- Modify: `src/spillover/retriever/budget.py`
- Modify: `src/spillover/retriever/render.py`
- Modify: `src/spillover/decay/scheduler.py`

- [ ] **Step 1: `retriever/budget.py` — one SELECT for all hits**

```python
from __future__ import annotations

import sqlite3

from spillover.eviction.tokenizer import count_tokens
from spillover.retriever.vector import Hit


def trim_to_budget(
    db: sqlite3.Connection,
    hits: list[Hit],
    max_tokens: int,
) -> list[Hit]:
    if max_tokens <= 0 or not hits:
        return []
    ids = [h.episode_id for h in hits]
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT id, token_count, content_json FROM episodes WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    out: list[Hit] = []
    total = 0
    for hit in hits:
        row = by_id.get(hit.episode_id)
        if row is None:
            continue
        n = int(row["token_count"]) if row["token_count"] else count_tokens(row["content_json"])
        if total + n > max_tokens:
            break
        total += n
        out.append(hit)
    return out
```

- [ ] **Step 2: `retriever/render.py` — one SELECT for all hits**

```python
from __future__ import annotations

import json
import sqlite3

from spillover.retriever.vector import Hit


def render_ltm_block(db: sqlite3.Connection, hits: list[Hit]) -> str:
    if not hits:
        return ""
    ids = [h.episode_id for h in hits]
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT id, role, content_json, memory_type FROM episodes "
        f"WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    sections: list[str] = []
    for hit in hits:
        row = by_id.get(hit.episode_id)
        if row is None:
            continue
        content = json.loads(row["content_json"])
        if isinstance(content, list):
            text = "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        else:
            text = str(content)
        sections.append(
            f'<episode id="{hit.episode_id}" type="{row["memory_type"]}" '
            f'role="{row["role"]}">\n{text}\n</episode>'
        )
    if not sections:
        return ""
    return (
        "<spillover-ltm>\n"
        "The following are relevant past episodes retrieved from long-term memory.\n"
        "They are NOT part of the active conversation.\n\n"
        + "\n\n".join(sections)
        + "\n</spillover-ltm>"
    )
```

- [ ] **Step 3: `decay/scheduler.py` — JOIN instead of N+1**

Replace the `_apply_decay_for_project` body:

```python
def _apply_decay_for_project(db_root: Path, project_id: str) -> int:
    db = open_project_db(db_root, project_id)
    n = 0
    try:
        rows = db.execute(
            "SELECT ve.episode_id, ve.memory_type, ve.ts, "
            "       COALESCE(e.pinned, 0) AS pinned, "
            "       COALESCE(e.hit_count, 0) AS hit_count "
            "FROM vec_episodes ve "
            "LEFT JOIN episodes e ON e.id = ve.episode_id "
            "WHERE ve.memory_type IS NOT NULL"
        ).fetchall()
        now_ms = int(time.time() * 1000)
        updates: list[tuple[float, str]] = []
        for r in rows:
            if int(r["pinned"]) == 1:
                continue
            age_hours = max(0, (now_ms - int(r["ts"])) / 1000 / 3600)
            half_life = HALF_LIFE_HOURS.get(r["memory_type"], 24)
            decay = math.exp(-age_hours / half_life)
            base = {
                "priority": 1.0,
                "procedural": 0.7,
                "semantic": 0.6,
                "episodic": 0.5,
            }.get(r["memory_type"], 0.5)
            hit_count = int(r["hit_count"])
            new_imp = min(1.0, base * decay + min(hit_count * 0.05, 0.5))
            updates.append((new_imp, r["episode_id"]))
            n += 1
        if updates:
            db.executemany(
                "UPDATE vec_episodes SET importance=? WHERE episode_id=?",
                updates,
            )
    finally:
        db.close()
    return n
```

- [ ] **Step 4: Run + commit**

```
python -m pytest -v -m "not slow"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "perf: batch SELECT in retriever/budget + retriever/render + decay (kill N+1)"
```

---

## Phase 2 — I4: httpx retry with backoff

### Task 3: Retry idempotent 5xx/timeouts

**Files:**
- Modify: `src/spillover/proxy/app.py`
- Create: `src/spillover/proxy/retry.py`
- Create: `tests/unit/test_retry.py`

- [ ] **Step 1: `src/spillover/proxy/retry.py`**

```python
from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, TypeVar

import httpx

from spillover.logging import get_logger

log = get_logger("retry")
T = TypeVar("T")

_RETRYABLE_STATUS = {429, 502, 503, 504}
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    cap: float = 16.0,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await fn()
        except _RETRYABLE_EXC as e:
            last_exc = e
            if attempt == max_attempts:
                raise
        else:
            if isinstance(result, httpx.Response) and result.status_code in _RETRYABLE_STATUS:
                if attempt == max_attempts:
                    return result
            else:
                return result
        delay = min(cap, base_delay * (4 ** (attempt - 1)))
        jitter = random.uniform(0, delay * 0.1)
        log.warning("retry attempt=%d delay=%.2fs", attempt, delay + jitter)
        await asyncio.sleep(delay + jitter)
    if last_exc is not None:
        raise last_exc
    return result  # type: ignore[return-value]
```

- [ ] **Step 2: Test**

```python
import httpx
import pytest

from spillover.proxy.retry import with_retry


@pytest.mark.asyncio
async def test_returns_immediately_on_2xx():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"ok": True})

    r = await with_retry(fn)
    assert r.status_code == 200
    assert calls == 1


@pytest.mark.asyncio
async def test_retries_on_503_until_success():
    calls = 0

    async def fn():
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200)

    r = await with_retry(fn, base_delay=0.01, cap=0.05)
    assert r.status_code == 200
    assert calls == 3


@pytest.mark.asyncio
async def test_returns_last_5xx_after_exhaustion():
    async def fn():
        return httpx.Response(503)

    r = await with_retry(fn, max_attempts=2, base_delay=0.01, cap=0.05)
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_raises_after_repeated_connect_error():
    async def fn():
        raise httpx.ConnectError("boom")

    with pytest.raises(httpx.ConnectError):
        await with_retry(fn, max_attempts=2, base_delay=0.01, cap=0.05)
```

- [ ] **Step 3: Wire into `proxy/app.py`**

Inside `_handle_request`, wrap the non-streaming forward in `with_retry`:

```python
from spillover.proxy.retry import with_retry


async def _post():
    return await app.state.http_client.post(
        upstream_url, headers=fwd_headers, content=forwarded_body
    )

r = await with_retry(_post)
```

Streaming branch stays single-attempt (streams are stateful and don't replay
cleanly).

- [ ] **Step 4: Run + commit**

```
python -m pytest tests/unit/test_retry.py -v
python -m pytest -v -m "not slow"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(proxy): httpx retry policy (3x exponential backoff on 429/5xx + timeouts)"
```

---

## Phase 3 — I7: redaction helper

### Task 4: `logging.redact(headers)` so future log calls cannot leak bearer tokens

**Files:**
- Modify: `src/spillover/logging.py`
- Create: `tests/unit/test_logging_redact.py`

- [ ] **Step 1: Extend `logging.py`**

Append:

```python
_REDACT_HEADERS = {
    "authorization",
    "x-api-key",
    "anthropic-api-key",
    "openai-api-key",
    "cookie",
    "set-cookie",
}


def redact(headers: dict | None) -> dict:
    """Return a copy of the headers dict with sensitive values masked."""
    if not headers:
        return {}
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _REDACT_HEADERS:
            if isinstance(v, str) and len(v) > 12:
                out[k] = v[:6] + "..." + v[-3:]
            else:
                out[k] = "***"
        else:
            out[k] = v
    return out
```

- [ ] **Step 2: Test**

```python
from spillover.logging import redact


def test_redact_authorization_bearer():
    out = redact({"Authorization": "Bearer sk-ant-XXXXXXXXXXXX"})
    assert "sk-ant-XXXXXXXXXXXX" not in out["Authorization"]
    assert out["Authorization"].startswith("Bearer")
    assert out["Authorization"].endswith("XXX")


def test_redact_lowercase_x_api_key():
    out = redact({"x-api-key": "abcdefghij"})
    assert out["x-api-key"] != "abcdefghij"


def test_redact_passes_other_headers_through():
    out = redact({"X-Project": "proj-1", "Content-Type": "application/json"})
    assert out["X-Project"] == "proj-1"
    assert out["Content-Type"] == "application/json"


def test_redact_handles_none_and_empty():
    assert redact(None) == {}
    assert redact({}) == {}
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_logging_redact.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(logging): redact() helper for Authorization / x-api-key / cookie"
```

---

## Phase 4 — M9: schedule prune of `seen_turns`

### Task 5: Wire `prune_old_seen_turns` into the decay tick

**Files:**
- Modify: `src/spillover/decay/scheduler.py`
- Modify: `tests/unit/test_decay_scheduler.py`

- [ ] **Step 1: Add `_prune_seen_turns_for_project` to decay tick**

In `decay/scheduler.py`, modify `DecayScheduler._tick`:

```python
    async def _tick(self) -> None:
        projects_dir = self.db_root / "projects"
        if not projects_dir.exists():
            return
        loop = asyncio.get_running_loop()
        for pdir in projects_dir.iterdir():
            if not pdir.is_dir():
                continue
            pid = pdir.name
            n_decayed = await loop.run_in_executor(
                None, _apply_decay_for_project, self.db_root, pid
            )
            n_pruned = await loop.run_in_executor(
                None, _prune_seen_turns_for_project, self.db_root, pid
            )
            if n_decayed > 0 or n_pruned > 0:
                log.info(
                    "decay project=%s decayed=%d pruned=%d",
                    pid, n_decayed, n_pruned,
                )
```

And add the helper:

```python
def _prune_seen_turns_for_project(db_root: Path, project_id: str) -> int:
    from spillover.counter_compact.detection import prune_old_seen_turns
    db = open_project_db(db_root, project_id)
    try:
        return prune_old_seen_turns(db, project_id, ttl_hours=72)
    finally:
        db.close()
```

- [ ] **Step 2: Test**

Append to `tests/unit/test_decay_scheduler.py`:

```python
def test_decay_tick_also_prunes_seen_turns(tmp_path):
    from spillover.counter_compact.detection import record_seen_turns
    from spillover.decay.scheduler import _prune_seen_turns_for_project
    from spillover.storage.sqlite import open_project_db

    db = open_project_db(tmp_path, "p1")
    try:
        record_seen_turns(db, "p1", [{"role": "assistant", "content": "old"}])
        db.execute("UPDATE seen_turns SET last_seen_ts=0 WHERE project_id=?", ("p1",))
    finally:
        db.close()
    pruned = _prune_seen_turns_for_project(tmp_path, "p1")
    assert pruned == 1
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_decay_scheduler.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(decay): tick also prunes seen_turns past compaction TTL"
```

---

## Phase 5 — Verify + tag v1.2.0 + push

### Task 6: Full suite + tag + push

- [ ] **Step 1: Full suite**

```
python -m pytest -v -m "not slow"
```

Expected: ~180 fast PASSED.

```
python -m pytest -v
```

Expected: ~186 PASSED.

```
python -m ruff check src/ tests/
```

Expected: 0 errors.

- [ ] **Step 2: Tag + push**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit --allow-empty -m "chore: spillover v1.2.0 (Plan 6 done -- polish + README + N+1 fixes + retry + redact + prune)"
git tag -a v1.2.0 -m "spillover v1.2.0 - polish (N+1 SELECT, httpx retry, header redact, seen_turns prune, README normalize)"
git push -u origin feat/plan6-polish-and-readme
git push origin --tags
```

- [ ] **Step 3: Merge to master + push**

```
git checkout master
git merge --no-ff feat/plan6-polish-and-readme -m "Merge Plan 6: polish + README normalize (v1.2.0)"
git push origin master
git push origin --tags
```

---

## Definition of Done

1. All tests pass (≥180 fast, ≥186 with slow).
2. `ruff check src/ tests/` exits 0.
3. `README.md` documents v1.1.0+ reality with: status, what it does, install, run, config table, commands, architecture, endpoints, status table, roadmap, papers, license.
4. I3 fixed: budget/render do batch SELECT; decay does one JOIN; verify by inspecting the new code.
5. I4 fixed: httpx retry policy applied to non-streaming forward.
6. I7 fixed: `redact()` available in `spillover.logging`.
7. M9 fixed: decay scheduler also prunes `seen_turns`.
8. `v1.2.0` tag exists locally and on remote.
9. `feat/plan6-polish-and-readme` pushed; `master` updated and pushed.

End of plan.
