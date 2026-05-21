# spillover MVP Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a transparent HTTP proxy that sits between any Anthropic-API client (Claude Code, raw SDK scripts) and the Anthropic Messages API, archives evicted conversation turns as raw episodes in a per-project SQLite database, and applies token-balanced 1:1 eviction once the active window reaches a watermark — without yet doing retrieval, facet extraction, or counter-compaction work.

**Architecture:** FastAPI + asyncio HTTP server listening on a configurable port. A wrapper command-line shim launches the target CLI under modified env vars (`ANTHROPIC_BASE_URL`) so the CLI's outbound requests are routed through this proxy. The proxy identifies the project via an `X-Project` header (a sha1 of the wrapper's `cwd`), opens a per-project SQLite DB on demand, forwards the request unchanged to Anthropic, streams the response back to the client, then measures token usage. Once usage crosses the watermark, the post-response background worker selects oldest evictable turns (FIFO, exclusions for system / recent buffer / pinned / priority-type) totaling `tokens_to_free = new_user_tokens + new_assistant_tokens`, writes them to the `episodes` table, and marks them evicted. The next inbound request omits evicted turns at the adapter level (later plans handle re-injection via retrieval). No graph DB or vector DB in this plan — those are added in Plan 2.

**Tech Stack:**
- Python 3.11+
- FastAPI + uvicorn (HTTP server)
- httpx (outbound to Anthropic, streaming)
- sqlite3 (stdlib, WAL mode)
- anthropic Python SDK (only for the tokenizer)
- click (CLI)
- pytest + pytest-asyncio (tests)
- respx (httpx mocking)

**Scope NOT covered in this plan (deferred to later plans):**
- Vector storage / sqlite-vec
- Kuzu graph DB
- Facet extraction (embeddings, NER, decision parser, classifier)
- Hybrid retrieval / RRF
- LTM injection into outbound payloads
- Counter-compaction defenses (usage rewrite, env-var disable, intercept, conversation diff)
- OpenAI adapter
- Decay scheduler
- Wrappers for non-Claude-Code CLIs
- Observability metrics (Prometheus)
- A/B benchmark

The end state of this plan is a runnable proxy that:
- Passes traffic transparently from Claude Code to Anthropic.
- Persists every assistant turn into the `episodes` table once it crosses the watermark.
- Maintains the 1:1 token-balance invariant.
- Has full unit + integration test coverage of the eviction selector, tokenizer, archive writer, and adapter parse/build paths.
- Ships an installable `spillover` command that starts the proxy and a `spillover stats <project>` subcommand that reports archived-episode counts.

---

## File structure

Files created in this plan:

```
spillover/
  pyproject.toml                          # NEW
  README.md                               # NEW (minimal stub, expanded in later plans)
  src/spillover/__init__.py               # NEW
  src/spillover/config.py                 # NEW (env-driven config object)
  src/spillover/cli.py                    # NEW (click entry-point)
  src/spillover/proxy/__init__.py         # NEW
  src/spillover/proxy/app.py              # NEW (FastAPI app)
  src/spillover/proxy/middleware.py       # NEW (project_id resolution from X-Project header)
  src/spillover/proxy/streaming.py        # NEW (SSE pass-through helpers)
  src/spillover/adapters/__init__.py      # NEW
  src/spillover/adapters/base.py          # NEW (Adapter ABC + Conversation dataclass)
  src/spillover/adapters/anthropic.py     # NEW (Anthropic Messages adapter)
  src/spillover/storage/__init__.py       # NEW
  src/spillover/storage/sqlite.py         # NEW (per-project DB handle factory)
  src/spillover/storage/schema.sql        # NEW (table definitions)
  src/spillover/archive/__init__.py       # NEW
  src/spillover/archive/writer.py         # NEW (archive_raw())
  src/spillover/eviction/__init__.py      # NEW
  src/spillover/eviction/tokenizer.py     # NEW (count_tokens())
  src/spillover/eviction/selector.py      # NEW (3-pass selector)
  tests/__init__.py                       # NEW
  tests/conftest.py                       # NEW (shared fixtures)
  tests/unit/__init__.py                  # NEW
  tests/unit/test_tokenizer.py            # NEW
  tests/unit/test_eviction_selector.py    # NEW
  tests/unit/test_adapter_anthropic.py    # NEW
  tests/unit/test_archive_writer.py       # NEW
  tests/unit/test_middleware.py           # NEW
  tests/integration/__init__.py           # NEW
  tests/integration/test_proxy_passthrough.py  # NEW
  tests/integration/test_eviction_lifecycle.py # NEW
```

Each file has one clear responsibility:
- `config.py` — single source of truth for env-driven settings (watermark, window_max, port, db_root)
- `cli.py` — argparse/click surface; never imports business logic directly, calls into proxy + storage modules
- `proxy/app.py` — defines FastAPI app + the single catch-all route `/v1/messages`; orchestrates middleware → adapter → forward → eviction
- `proxy/middleware.py` — extracts and validates `X-Project` header, attaches `project_id` to request state
- `proxy/streaming.py` — wraps httpx streaming so the response body is duplicated (one copy to client, one to the eviction worker)
- `adapters/base.py` — `Adapter` ABC and the provider-neutral `Conversation` dataclass
- `adapters/anthropic.py` — parses inbound Anthropic Messages JSON into `Conversation`, builds outbound JSON back, computes per-turn tokens via the Anthropic SDK tokenizer
- `storage/sqlite.py` — opens a per-project SQLite file under `~/.spillover/projects/<project_id>/episodes.db`, applies `schema.sql`, enables WAL mode
- `storage/schema.sql` — DDL for `episodes` and `seen_turns` tables (the latter is created here but only used in Plan 3)
- `archive/writer.py` — `archive_raw(db, turn)` inserts an `episodes` row and returns the new id
- `eviction/tokenizer.py` — wraps Anthropic SDK token counting; memoizes by content hash
- `eviction/selector.py` — implements the 3-pass selection from the spec (FIFO non-priority → priority fallback → budget-pressure fallback)

---

## Phase 0 — Project bootstrap

### Task 1: Initialize pyproject + repo layout

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/spillover/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`

- [ ] **Step 1: Write `pyproject.toml`**

Content:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "spillover"
version = "0.1.0"
description = "Transparent LLM proxy with overflow memory architecture"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "Proprietary" }
authors = [{ name = "Luiz Henrique Cavalcanti Ramos da Silva", email = "luizhcrs@gmail.com" }]
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "httpx>=0.27",
    "anthropic>=0.34",
    "click>=8.1",
    "pydantic>=2.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "respx>=0.20",
    "ruff>=0.4",
    "mypy>=1.10",
]

[project.scripts]
spillover = "spillover.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/spillover"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-ra -q"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B"]
```

- [ ] **Step 2: Write `README.md`** (minimal stub, expanded in later plans)

Content:

```markdown
# spillover

Transparent LLM proxy with overflow memory architecture.

**Status:** v0.1 (MVP foundation only — see `docs/superpowers/plans/` for roadmap).

## Install

```bash
pip install -e ".[dev]"
```

## Run the proxy

```bash
spillover up
```

Then point your client at `http://127.0.0.1:8787`:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude code
```

See `docs/superpowers/specs/2026-05-20-spillover-design.md` for full architecture.
```

- [ ] **Step 3: Create empty `__init__.py` files**

```bash
mkdir -p src/spillover/proxy src/spillover/adapters src/spillover/storage src/spillover/archive src/spillover/eviction
touch src/spillover/__init__.py
touch src/spillover/proxy/__init__.py
touch src/spillover/adapters/__init__.py
touch src/spillover/storage/__init__.py
touch src/spillover/archive/__init__.py
touch src/spillover/eviction/__init__.py
touch tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
```

- [ ] **Step 4: Install in editable mode**

Run: `pip install -e ".[dev]"`
Expected: installs without error; `which spillover` resolves.

- [ ] **Step 5: Verify pytest discovers no tests yet**

Run: `pytest -q`
Expected: `no tests ran`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml README.md src/ tests/
git commit -m "chore: bootstrap spillover project layout"
```

---

### Task 2: Write `config.py`

**Files:**
- Create: `src/spillover/config.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_config.py`:

```python
import os
from spillover.config import Config

def test_config_defaults(monkeypatch):
    monkeypatch.delenv("SPILLOVER_PORT", raising=False)
    monkeypatch.delenv("SPILLOVER_WATERMARK", raising=False)
    monkeypatch.delenv("SPILLOVER_WINDOW_MAX", raising=False)
    monkeypatch.delenv("SPILLOVER_DB_ROOT", raising=False)
    monkeypatch.delenv("SPILLOVER_UPSTREAM_BASE_URL", raising=False)
    cfg = Config.from_env()
    assert cfg.port == 8787
    assert cfg.watermark == 0.85
    assert cfg.window_max == 200_000
    assert cfg.upstream_base_url == "https://api.anthropic.com"
    assert str(cfg.db_root).endswith(".spillover")

def test_config_env_overrides(monkeypatch):
    monkeypatch.setenv("SPILLOVER_PORT", "9000")
    monkeypatch.setenv("SPILLOVER_WATERMARK", "0.9")
    monkeypatch.setenv("SPILLOVER_WINDOW_MAX", "1000000")
    monkeypatch.setenv("SPILLOVER_UPSTREAM_BASE_URL", "https://example.com")
    cfg = Config.from_env()
    assert cfg.port == 9000
    assert cfg.watermark == 0.9
    assert cfg.window_max == 1_000_000
    assert cfg.upstream_base_url == "https://example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'spillover.config'`.

- [ ] **Step 3: Implement `config.py`**

`src/spillover/config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    port: int
    watermark: float
    window_max: int
    db_root: Path
    upstream_base_url: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            port=int(os.environ.get("SPILLOVER_PORT", "8787")),
            watermark=float(os.environ.get("SPILLOVER_WATERMARK", "0.85")),
            window_max=int(os.environ.get("SPILLOVER_WINDOW_MAX", "200000")),
            db_root=Path(os.environ.get("SPILLOVER_DB_ROOT", str(Path.home() / ".spillover"))),
            upstream_base_url=os.environ.get(
                "SPILLOVER_UPSTREAM_BASE_URL", "https://api.anthropic.com"
            ),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config.py -v`
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/spillover/config.py tests/unit/test_config.py
git commit -m "feat(config): env-driven Config dataclass"
```

---

## Phase 1 — Storage layer

### Task 3: SQLite schema (`episodes` + `seen_turns`)

**Files:**
- Create: `src/spillover/storage/schema.sql`
- Create: `src/spillover/storage/sqlite.py`
- Create: `tests/unit/test_storage_sqlite.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_storage_sqlite.py`:

```python
import sqlite3
from pathlib import Path

import pytest

from spillover.storage.sqlite import open_project_db, project_db_path


def test_project_db_path_uses_root(tmp_path):
    p = project_db_path(tmp_path, "abc123")
    assert p == tmp_path / "projects" / "abc123" / "episodes.db"


def test_open_project_db_creates_dir_and_tables(tmp_path):
    db = open_project_db(tmp_path, "abc123")
    try:
        cur = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert "episodes" in tables
        assert "seen_turns" in tables
        # WAL mode active
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        db.close()


def test_open_project_db_idempotent(tmp_path):
    open_project_db(tmp_path, "abc123").close()
    db = open_project_db(tmp_path, "abc123")
    try:
        # Re-opening does not error or wipe tables
        cur = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert "episodes" in tables
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_storage_sqlite.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `schema.sql`**

`src/spillover/storage/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS episodes (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    role            TEXT NOT NULL,
    content_json    TEXT NOT NULL,
    tool_calls_json TEXT,
    code_refs_json  TEXT,
    token_count     INTEGER NOT NULL,
    ts              INTEGER NOT NULL,
    hash            TEXT NOT NULL,
    evicted         INTEGER NOT NULL DEFAULT 0,
    pinned          INTEGER NOT NULL DEFAULT 0,
    hit_count       INTEGER NOT NULL DEFAULT 0,
    memory_type     TEXT,
    facet_pending   INTEGER NOT NULL DEFAULT 1,
    compaction_rescued INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_episodes_evicted_ts ON episodes(evicted, ts);
CREATE INDEX IF NOT EXISTS idx_episodes_hash ON episodes(hash);

CREATE TABLE IF NOT EXISTS seen_turns (
    project_id      TEXT NOT NULL,
    turn_hash       TEXT NOT NULL,
    turn_index      INTEGER NOT NULL,
    content_json    TEXT NOT NULL,
    first_seen_ts   INTEGER NOT NULL,
    last_seen_ts    INTEGER NOT NULL,
    PRIMARY KEY (project_id, turn_hash)
);

CREATE INDEX IF NOT EXISTS idx_seen_turns_last_seen ON seen_turns(last_seen_ts);
```

- [ ] **Step 4: Implement `sqlite.py`**

`src/spillover/storage/sqlite.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def project_db_path(db_root: Path, project_id: str) -> Path:
    return db_root / "projects" / project_id / "episodes.db"


def open_project_db(db_root: Path, project_id: str) -> sqlite3.Connection:
    path = project_db_path(db_root, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    return conn
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_storage_sqlite.py -v`
Expected: 3 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/spillover/storage/ tests/unit/test_storage_sqlite.py
git commit -m "feat(storage): SQLite per-project DB factory with WAL"
```

---

## Phase 2 — Tokenizer + archive writer

### Task 4: Tokenizer wrapper

**Files:**
- Create: `src/spillover/eviction/tokenizer.py`
- Create: `tests/unit/test_tokenizer.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_tokenizer.py`:

```python
from spillover.eviction.tokenizer import count_tokens


def test_count_tokens_simple_string():
    n = count_tokens("hello world")
    assert isinstance(n, int)
    assert n > 0
    assert n < 20  # sanity


def test_count_tokens_empty():
    assert count_tokens("") == 0


def test_count_tokens_anthropic_message():
    msg = {"role": "user", "content": "What's the capital of France?"}
    n = count_tokens(msg)
    assert n > 0


def test_count_tokens_memoized():
    s = "the quick brown fox " * 50
    n1 = count_tokens(s)
    n2 = count_tokens(s)
    assert n1 == n2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_tokenizer.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `tokenizer.py`**

`src/spillover/eviction/tokenizer.py`:

```python
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any


def _normalize(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, sort_keys=True, ensure_ascii=False)
    return str(content)


@lru_cache(maxsize=4096)
def _count_for_hash(content_hash: str, text: str) -> int:
    # Heuristic: 1 token per ~4 characters for English/code mix.
    # The Anthropic SDK does not expose a synchronous offline tokenizer in this
    # version; we use a stable approximation here and refine in Plan 2 when the
    # facet pipeline calls the real countTokens endpoint asynchronously.
    if not text:
        return 0
    return max(1, len(text) // 4)


def count_tokens(content: Any) -> int:
    text = _normalize(content)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return _count_for_hash(h, text)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_tokenizer.py -v`
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/spillover/eviction/tokenizer.py tests/unit/test_tokenizer.py
git commit -m "feat(tokenizer): char-based heuristic count_tokens with memoization"
```

---

### Task 5: Archive writer

**Files:**
- Create: `src/spillover/archive/writer.py`
- Create: `tests/unit/test_archive_writer.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_archive_writer.py`:

```python
import json
import time

from spillover.archive.writer import archive_raw, Turn
from spillover.storage.sqlite import open_project_db


def test_archive_raw_inserts_and_returns_id(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        turn = Turn(
            project_id="p1",
            role="assistant",
            content=[{"type": "text", "text": "hello"}],
            tool_calls=[],
            code_refs=[],
            token_count=5,
            ts=int(time.time() * 1000),
        )
        eid = archive_raw(db, turn)
        assert isinstance(eid, str)
        row = db.execute("SELECT * FROM episodes WHERE id = ?", (eid,)).fetchone()
        assert row is not None
        assert row["role"] == "assistant"
        assert json.loads(row["content_json"])[0]["text"] == "hello"
        assert row["evicted"] == 0
        assert row["facet_pending"] == 1
        assert row["token_count"] == 5
    finally:
        db.close()


def test_archive_raw_dedup_by_hash(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        turn = Turn(
            project_id="p1",
            role="user",
            content="same text",
            tool_calls=[],
            code_refs=[],
            token_count=2,
            ts=1700000000000,
        )
        eid1 = archive_raw(db, turn)
        eid2 = archive_raw(db, turn)
        assert eid1 == eid2
        count = db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        assert count == 1
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_archive_writer.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `writer.py`**

`src/spillover/archive/writer.py`:

```python
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Turn:
    project_id: str
    role: str
    content: Any
    tool_calls: list[dict] = field(default_factory=list)
    code_refs: list[dict] = field(default_factory=list)
    token_count: int = 0
    ts: int = 0


def _hash_turn(turn: Turn) -> str:
    payload = json.dumps(
        {
            "role": turn.role,
            "content": turn.content,
            "tool_calls": turn.tool_calls,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def archive_raw(db: sqlite3.Connection, turn: Turn) -> str:
    h = _hash_turn(turn)
    existing = db.execute("SELECT id FROM episodes WHERE hash = ?", (h,)).fetchone()
    if existing is not None:
        return existing["id"]
    eid = str(uuid.uuid4())
    db.execute(
        """
        INSERT INTO episodes (
            id, project_id, role, content_json, tool_calls_json,
            code_refs_json, token_count, ts, hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid,
            turn.project_id,
            turn.role,
            json.dumps(turn.content, ensure_ascii=False),
            json.dumps(turn.tool_calls, ensure_ascii=False),
            json.dumps(turn.code_refs, ensure_ascii=False),
            turn.token_count,
            turn.ts,
            h,
        ),
    )
    return eid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_archive_writer.py -v`
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/spillover/archive/writer.py tests/unit/test_archive_writer.py
git commit -m "feat(archive): archive_raw with sha256 dedup"
```

---

## Phase 3 — Eviction selector

### Task 6: 3-pass eviction selector

**Files:**
- Create: `src/spillover/eviction/selector.py`
- Create: `tests/unit/test_eviction_selector.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_eviction_selector.py`:

```python
from spillover.eviction.selector import (
    ActiveTurn,
    SelectionResult,
    select_for_eviction,
)


def _t(idx, tokens, role="user", pinned=False, memory_type=None, is_system=False):
    return ActiveTurn(
        index=idx,
        token_count=tokens,
        role=role,
        pinned=pinned,
        memory_type=memory_type,
        is_system=is_system,
    )


def test_pass1_fifo_non_priority():
    turns = [
        _t(0, 100, is_system=True),
        _t(1, 200),
        _t(2, 300),
        _t(3, 100, memory_type="priority"),
        _t(4, 400),
        _t(5, 50),  # recent buffer
        _t(6, 50),
        _t(7, 50),
        _t(8, 50),
    ]
    result = select_for_eviction(turns, tokens_to_free=400, recent_buffer=4)
    # Recent buffer = last 4 -> indexes 5,6,7,8 excluded
    # System excluded (0)
    # Priority excluded (3) on pass 1
    # FIFO over 1,2,4 -> 1 (200) + 2 (300) = 500 >= 400, stop
    assert result.evicted_indexes == [1, 2]
    assert result.tokens_freed == 500
    assert result.pass_used == 1


def test_pass2_priority_fallback_when_pass1_short():
    turns = [
        _t(0, 100, is_system=True),
        _t(1, 100, memory_type="priority"),
        _t(2, 200, memory_type="priority"),
        _t(3, 100),  # only 100 tokens non-priority available
        _t(4, 50),
        _t(5, 50),
        _t(6, 50),
        _t(7, 50),
    ]
    # Pass 1 finds only turn 3 (100 tokens) -> not enough for 250
    # Pass 2 includes priority oldest-first: 3 (100) + 1 (100) + 2 (200) -> stop at 400
    result = select_for_eviction(turns, tokens_to_free=250, recent_buffer=4)
    assert 3 in result.evicted_indexes
    assert 1 in result.evicted_indexes
    assert result.pass_used == 2
    assert result.tokens_freed >= 250


def test_pass3_budget_pressure_when_everything_protected():
    turns = [
        _t(0, 100, is_system=True),
        _t(1, 100, pinned=True),
        _t(2, 100, pinned=True),
        _t(3, 50),  # recent
        _t(4, 50),
        _t(5, 50),
        _t(6, 50),
    ]
    result = select_for_eviction(turns, tokens_to_free=300, recent_buffer=4)
    assert result.pass_used == 3
    assert result.budget_pressure is True
    # No turn evicted because pinned + system + recent cover everything
    assert result.evicted_indexes == []


def test_no_eviction_needed_below_threshold():
    # Caller must check fill_ratio before invoking; selector still returns empty
    # when tokens_to_free is 0
    turns = [_t(0, 100), _t(1, 100)]
    result = select_for_eviction(turns, tokens_to_free=0, recent_buffer=4)
    assert result.evicted_indexes == []
    assert result.tokens_freed == 0
    assert result.pass_used == 0


def test_token_balance_invariant_over_50_turns():
    """N tokens new in -> at least N tokens out (steady-state)."""
    turns = [_t(i, 100) for i in range(50)]
    result = select_for_eviction(turns, tokens_to_free=350, recent_buffer=4)
    assert result.tokens_freed >= 350
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_eviction_selector.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `selector.py`**

`src/spillover/eviction/selector.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ActiveTurn:
    index: int
    token_count: int
    role: str
    pinned: bool = False
    memory_type: str | None = None
    is_system: bool = False


@dataclass
class SelectionResult:
    evicted_indexes: list[int] = field(default_factory=list)
    tokens_freed: int = 0
    pass_used: int = 0
    budget_pressure: bool = False


def _evictable_pass1(turns: list[ActiveTurn], recent_buffer: int) -> list[ActiveTurn]:
    if not turns:
        return []
    cutoff = max(0, len(turns) - recent_buffer)
    return [
        t
        for i, t in enumerate(turns)
        if not t.is_system
        and not t.pinned
        and t.memory_type != "priority"
        and i < cutoff
    ]


def _evictable_pass2(turns: list[ActiveTurn], recent_buffer: int) -> list[ActiveTurn]:
    if not turns:
        return []
    cutoff = max(0, len(turns) - recent_buffer)
    return [
        t
        for i, t in enumerate(turns)
        if not t.is_system and not t.pinned and i < cutoff
    ]


def select_for_eviction(
    turns: list[ActiveTurn],
    tokens_to_free: int,
    recent_buffer: int = 4,
) -> SelectionResult:
    if tokens_to_free <= 0:
        return SelectionResult()

    evicted: list[int] = []
    freed = 0

    for t in _evictable_pass1(turns, recent_buffer):
        evicted.append(t.index)
        freed += t.token_count
        if freed >= tokens_to_free:
            return SelectionResult(
                evicted_indexes=evicted, tokens_freed=freed, pass_used=1
            )

    evicted = []
    freed = 0
    for t in _evictable_pass2(turns, recent_buffer):
        evicted.append(t.index)
        freed += t.token_count
        if freed >= tokens_to_free:
            return SelectionResult(
                evicted_indexes=evicted, tokens_freed=freed, pass_used=2
            )

    if freed >= tokens_to_free:
        return SelectionResult(
            evicted_indexes=evicted, tokens_freed=freed, pass_used=2
        )

    return SelectionResult(
        evicted_indexes=[],
        tokens_freed=0,
        pass_used=3,
        budget_pressure=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_eviction_selector.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/spillover/eviction/selector.py tests/unit/test_eviction_selector.py
git commit -m "feat(eviction): 3-pass selector with token-balance invariant"
```

---

## Phase 4 — Anthropic adapter

### Task 7: Adapter ABC + Conversation dataclass

**Files:**
- Create: `src/spillover/adapters/base.py`
- Create: `tests/unit/test_adapter_base.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_adapter_base.py`:

```python
from spillover.adapters.base import Conversation, ConversationTurn


def test_conversation_turn_fields():
    t = ConversationTurn(
        role="user",
        content=[{"type": "text", "text": "hi"}],
        tool_calls=[],
        token_count=3,
    )
    assert t.role == "user"
    assert t.token_count == 3


def test_conversation_total_tokens():
    c = Conversation(
        system="be helpful",
        system_tokens=5,
        turns=[
            ConversationTurn(role="user", content="a", tool_calls=[], token_count=2),
            ConversationTurn(role="assistant", content="b", tool_calls=[], token_count=4),
        ],
    )
    assert c.total_input_tokens == 5 + 2 + 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_adapter_base.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `base.py`**

`src/spillover/adapters/base.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationTurn:
    role: str
    content: Any
    tool_calls: list[dict] = field(default_factory=list)
    token_count: int = 0
    source_index: int | None = None  # original position in inbound payload


@dataclass
class Conversation:
    system: str | list[dict] | None = None
    system_tokens: int = 0
    turns: list[ConversationTurn] = field(default_factory=list)
    model: str | None = None
    max_tokens: int = 4096
    extra: dict = field(default_factory=dict)  # provider-specific passthrough

    @property
    def total_input_tokens(self) -> int:
        return self.system_tokens + sum(t.token_count for t in self.turns)


class Adapter(ABC):
    @abstractmethod
    def parse(self, payload: dict) -> Conversation:
        ...

    @abstractmethod
    def build(self, conversation: Conversation) -> dict:
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_adapter_base.py -v`
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/spillover/adapters/base.py tests/unit/test_adapter_base.py
git commit -m "feat(adapter): Conversation + ConversationTurn + Adapter ABC"
```

---

### Task 8: Anthropic adapter — `parse()`

**Files:**
- Create: `src/spillover/adapters/anthropic.py`
- Create: `tests/unit/test_adapter_anthropic.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_adapter_anthropic.py`:

```python
from spillover.adapters.anthropic import AnthropicAdapter


def test_parse_minimal():
    payload = {
        "model": "claude-opus-4-7",
        "max_tokens": 1024,
        "system": "you are helpful",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    }
    adapter = AnthropicAdapter()
    conv = adapter.parse(payload)
    assert conv.model == "claude-opus-4-7"
    assert conv.max_tokens == 1024
    assert conv.system == "you are helpful"
    assert conv.system_tokens > 0
    assert len(conv.turns) == 2
    assert conv.turns[0].role == "user"
    assert conv.turns[1].role == "assistant"
    assert all(t.token_count > 0 for t in conv.turns)


def test_parse_content_blocks():
    payload = {
        "model": "claude-opus-4-7",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "thinking..."},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "Read",
                        "input": {"file_path": "/x"},
                    },
                ],
            }
        ],
    }
    conv = AnthropicAdapter().parse(payload)
    assert len(conv.turns) == 1
    assert len(conv.turns[0].tool_calls) == 1
    assert conv.turns[0].tool_calls[0]["name"] == "Read"


def test_parse_extra_preserved():
    payload = {
        "model": "claude-opus-4-7",
        "max_tokens": 1024,
        "stream": True,
        "metadata": {"user_id": "abc"},
        "messages": [{"role": "user", "content": "hi"}],
    }
    conv = AnthropicAdapter().parse(payload)
    assert conv.extra.get("stream") is True
    assert conv.extra.get("metadata") == {"user_id": "abc"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_adapter_anthropic.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `anthropic.py` — parse only**

`src/spillover/adapters/anthropic.py`:

```python
from __future__ import annotations

from typing import Any

from spillover.adapters.base import Adapter, Conversation, ConversationTurn
from spillover.eviction.tokenizer import count_tokens

_PASSTHROUGH_KEYS = {
    "stream",
    "stop_sequences",
    "temperature",
    "top_p",
    "top_k",
    "metadata",
    "tools",
    "tool_choice",
    "thinking",
    "anthropic_version",
}


class AnthropicAdapter(Adapter):
    def parse(self, payload: dict) -> Conversation:
        system = payload.get("system")
        system_tokens = count_tokens(system) if system else 0

        turns: list[ConversationTurn] = []
        for i, msg in enumerate(payload.get("messages", [])):
            content = msg["content"]
            tool_calls = self._extract_tool_calls(content)
            tok = count_tokens(content)
            turns.append(
                ConversationTurn(
                    role=msg["role"],
                    content=content,
                    tool_calls=tool_calls,
                    token_count=tok,
                    source_index=i,
                )
            )

        extra = {k: payload[k] for k in _PASSTHROUGH_KEYS if k in payload}

        return Conversation(
            system=system,
            system_tokens=system_tokens,
            turns=turns,
            model=payload.get("model"),
            max_tokens=payload.get("max_tokens", 4096),
            extra=extra,
        )

    def build(self, conversation: Conversation) -> dict:
        raise NotImplementedError("build implemented in next task")

    def _extract_tool_calls(self, content: Any) -> list[dict]:
        if not isinstance(content, list):
            return []
        return [
            {"id": b.get("id"), "name": b.get("name"), "input": b.get("input")}
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_adapter_anthropic.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/spillover/adapters/anthropic.py tests/unit/test_adapter_anthropic.py
git commit -m "feat(adapter/anthropic): parse Messages payload into Conversation"
```

---

### Task 9: Anthropic adapter — `build()`

**Files:**
- Modify: `src/spillover/adapters/anthropic.py`
- Modify: `tests/unit/test_adapter_anthropic.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_adapter_anthropic.py`:

```python
from spillover.adapters.base import Conversation, ConversationTurn


def test_build_roundtrip_preserves_payload():
    payload = {
        "model": "claude-opus-4-7",
        "max_tokens": 1024,
        "stream": True,
        "system": "be brief",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    }
    adapter = AnthropicAdapter()
    conv = adapter.parse(payload)
    rebuilt = adapter.build(conv)
    assert rebuilt["model"] == payload["model"]
    assert rebuilt["max_tokens"] == payload["max_tokens"]
    assert rebuilt["system"] == payload["system"]
    assert rebuilt["stream"] is True
    assert len(rebuilt["messages"]) == 2
    assert rebuilt["messages"][0] == {"role": "user", "content": "hi"}


def test_build_drops_evicted_turns():
    conv = Conversation(
        system="s",
        system_tokens=1,
        turns=[
            ConversationTurn(role="user", content="A", tool_calls=[], token_count=1),
            ConversationTurn(role="assistant", content="B", tool_calls=[], token_count=1),
            ConversationTurn(role="user", content="C", tool_calls=[], token_count=1),
        ],
        model="claude-opus-4-7",
        max_tokens=1024,
    )
    # Drop middle turn
    conv.turns.pop(1)
    rebuilt = AnthropicAdapter().build(conv)
    assert [m["content"] for m in rebuilt["messages"]] == ["A", "C"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_adapter_anthropic.py -v`
Expected: 2 new tests FAIL (NotImplementedError).

- [ ] **Step 3: Implement `build()` in `anthropic.py`**

Replace the `build` method body:

```python
    def build(self, conversation: Conversation) -> dict:
        payload: dict = {
            "model": conversation.model,
            "max_tokens": conversation.max_tokens,
            "messages": [
                {"role": t.role, "content": t.content} for t in conversation.turns
            ],
        }
        if conversation.system is not None:
            payload["system"] = conversation.system
        payload.update(conversation.extra)
        return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_adapter_anthropic.py -v`
Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/spillover/adapters/anthropic.py tests/unit/test_adapter_anthropic.py
git commit -m "feat(adapter/anthropic): build Conversation back to Messages payload"
```

---

## Phase 5 — Proxy core

### Task 10: Project_id middleware

**Files:**
- Create: `src/spillover/proxy/middleware.py`
- Create: `tests/unit/test_middleware.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_middleware.py`:

```python
import hashlib

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from spillover.proxy.middleware import ProjectIdMiddleware


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(ProjectIdMiddleware)

    @app.get("/echo")
    async def echo(request: Request):
        return JSONResponse({"project_id": request.state.project_id})

    return TestClient(app)


def test_middleware_passes_x_project(client):
    r = client.get("/echo", headers={"X-Project": "deadbeef"})
    assert r.status_code == 200
    assert r.json()["project_id"] == "deadbeef"


def test_middleware_hashes_arbitrary_path_when_unhashed(client):
    raw = "/Users/luiz/Documents/Projects/agente-imobiliaria"
    expected = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    r = client.get("/echo", headers={"X-Project": raw})
    assert r.json()["project_id"] == expected


def test_middleware_400_when_missing(client):
    r = client.get("/echo")
    assert r.status_code == 400
    assert "X-Project" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_middleware.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `middleware.py`**

`src/spillover/proxy/middleware.py`:

```python
from __future__ import annotations

import hashlib
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_HEX40 = re.compile(r"^[0-9a-f]{40}$")


class ProjectIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        raw = request.headers.get("x-project")
        if not raw:
            return JSONResponse(
                {"error": "missing X-Project header"}, status_code=400
            )
        if _HEX40.match(raw):
            project_id = raw
        else:
            project_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        request.state.project_id = project_id
        return await call_next(request)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_middleware.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/spillover/proxy/middleware.py tests/unit/test_middleware.py
git commit -m "feat(proxy): X-Project header middleware"
```

---

### Task 11: Streaming pass-through helper

**Files:**
- Create: `src/spillover/proxy/streaming.py`
- Create: `tests/unit/test_streaming.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_streaming.py`:

```python
import asyncio

import pytest

from spillover.proxy.streaming import duplicate_stream


@pytest.mark.asyncio
async def test_duplicate_stream_yields_chunks_and_captures():
    async def source():
        for chunk in [b"a", b"bc", b"def"]:
            yield chunk

    captured: list[bytes] = []
    out: list[bytes] = []
    async for chunk in duplicate_stream(source(), captured):
        out.append(chunk)
    assert out == [b"a", b"bc", b"def"]
    assert b"".join(captured) == b"abcdef"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_streaming.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `streaming.py`**

`src/spillover/proxy/streaming.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator


async def duplicate_stream(
    source: AsyncIterator[bytes],
    sink: list[bytes],
) -> AsyncIterator[bytes]:
    async for chunk in source:
        sink.append(chunk)
        yield chunk
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_streaming.py -v`
Expected: 1 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/spillover/proxy/streaming.py tests/unit/test_streaming.py
git commit -m "feat(proxy): duplicate_stream helper for response capture"
```

---

### Task 12: FastAPI app — passthrough route

**Files:**
- Create: `src/spillover/proxy/app.py`
- Create: `tests/integration/test_proxy_passthrough.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the conftest fixtures**

`tests/conftest.py`:

```python
import pytest

from spillover.config import Config


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    monkeypatch.setenv("SPILLOVER_WINDOW_MAX", "1000")
    monkeypatch.setenv("SPILLOVER_WATERMARK", "0.85")
    return Config.from_env()
```

- [ ] **Step 2: Write the failing integration test**

`tests/integration/test_proxy_passthrough.py`:

```python
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app


@pytest.fixture
def client(config):
    app = create_app(config)
    return TestClient(app)


@respx.mock
def test_passthrough_non_streaming(client):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_1",
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "hi back"}],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        )
    )
    r = client.post(
        "/v1/messages",
        headers={
            "X-Project": "proj_test",
            "Authorization": "Bearer test-key",
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content"][0]["text"] == "hi back"


@respx.mock
def test_passthrough_streaming(client):
    sse_body = (
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
        b'event: content_block_delta\ndata: {"delta":{"text":"hi"}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop","usage":{"input_tokens":5,"output_tokens":1}}\n\n'
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=sse_body,
            headers={"content-type": "text/event-stream"},
        )
    )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "proj_test", "Authorization": "Bearer test"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    assert b"message_stop" in r.content
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/integration/test_proxy_passthrough.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'spillover.proxy.app'`.

- [ ] **Step 4: Implement `app.py`**

`src/spillover/proxy/app.py`:

```python
from __future__ import annotations

import json

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from spillover.config import Config
from spillover.proxy.middleware import ProjectIdMiddleware
from spillover.proxy.streaming import duplicate_stream


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="spillover", version="0.1.0")
    app.add_middleware(ProjectIdMiddleware)
    app.state.config = config
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    @app.on_event("shutdown")
    async def _close():
        await app.state.http_client.aclose()

    @app.post("/v1/messages")
    async def messages(request: Request):
        body = await request.body()
        payload = json.loads(body)
        upstream_url = f"{config.upstream_base_url}/v1/messages"
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in {"host", "content-length", "x-project"}
        }
        is_stream = bool(payload.get("stream"))

        if not is_stream:
            r = await app.state.http_client.post(
                upstream_url, headers=fwd_headers, content=body
            )
            return JSONResponse(
                content=r.json(),
                status_code=r.status_code,
                headers={"content-type": "application/json"},
            )

        async def proxy_stream():
            async with app.state.http_client.stream(
                "POST", upstream_url, headers=fwd_headers, content=body
            ) as r:
                sink: list[bytes] = []
                async for chunk in duplicate_stream(r.aiter_bytes(), sink):
                    yield chunk

        return StreamingResponse(proxy_stream(), media_type="text/event-stream")

    return app
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/integration/test_proxy_passthrough.py -v`
Expected: 2 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/spillover/proxy/app.py tests/integration/test_proxy_passthrough.py tests/conftest.py
git commit -m "feat(proxy): passthrough /v1/messages with streaming support"
```

---

## Phase 6 — Eviction lifecycle wired into proxy

### Task 13: Post-response eviction hook

**Files:**
- Modify: `src/spillover/proxy/app.py`
- Create: `tests/integration/test_eviction_lifecycle.py`

This task wires the adapter, eviction selector, archive writer, and storage layer into the proxy. After every response (streaming or not), the proxy parses the upstream `usage` reply, decides if eviction is needed, selects turns to evict, and archives them in the project DB.

- [ ] **Step 1: Write the failing integration test**

`tests/integration/test_eviction_lifecycle.py`:

```python
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app
from spillover.storage.sqlite import open_project_db


@pytest.fixture
def client(config):
    return TestClient(create_app(config))


def _upstream_resp(input_tokens: int, output_tokens: int, text: str = "ok"):
    return httpx.Response(
        200,
        json={
            "id": "msg_test",
            "model": "claude-opus-4-7",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        },
    )


@respx.mock
def test_no_eviction_below_watermark(client, config):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_upstream_resp(500, 50)  # 550 of 1000 -> 0.55 < 0.85
    )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "p1", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "x" * 100}],
        },
    )
    assert r.status_code == 200

    db = open_project_db(config.db_root, "p1")
    try:
        count = db.execute("SELECT COUNT(*) FROM episodes WHERE evicted=1").fetchone()[0]
        assert count == 0
    finally:
        db.close()


@respx.mock
def test_eviction_triggers_above_watermark(client, config):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_upstream_resp(900, 80)  # 980 of 1000 -> 0.98 > 0.85
    )
    # Big conversation: 12 turns, each ~80 tokens
    messages = []
    for i in range(12):
        messages.append(
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 320}
        )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "p2", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": messages,
        },
    )
    assert r.status_code == 200

    db = open_project_db(config.db_root, "p2")
    try:
        evicted = db.execute(
            "SELECT COUNT(*) FROM episodes WHERE evicted=1"
        ).fetchone()[0]
        assert evicted > 0
        # Token balance: tokens_freed >= tokens_in_this_turn
        # tokens_in_this_turn = new_user_tokens + new_assistant_tokens
        # For this test the "new" tokens are the last user turn + assistant response
        freed = db.execute(
            "SELECT SUM(token_count) FROM episodes WHERE evicted=1"
        ).fetchone()[0]
        assert freed >= 80  # last turn ~80
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_eviction_lifecycle.py -v`
Expected: FAIL — eviction not wired yet.

- [ ] **Step 3: Extend `app.py` with eviction hook**

Replace `src/spillover/proxy/app.py` entirely:

```python
from __future__ import annotations

import json
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from spillover.adapters.anthropic import AnthropicAdapter
from spillover.archive.writer import Turn, archive_raw
from spillover.config import Config
from spillover.eviction.selector import ActiveTurn, select_for_eviction
from spillover.eviction.tokenizer import count_tokens
from spillover.proxy.middleware import ProjectIdMiddleware
from spillover.proxy.streaming import duplicate_stream
from spillover.storage.sqlite import open_project_db


def _extract_usage_non_streaming(body: bytes) -> tuple[int, int] | None:
    try:
        data = json.loads(body)
    except Exception:
        return None
    usage = data.get("usage")
    if not usage:
        return None
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def _extract_usage_sse(captured: list[bytes]) -> tuple[int, int] | None:
    """Walk captured SSE chunks for the message_stop / message_delta usage."""
    joined = b"".join(captured).decode("utf-8", errors="replace")
    input_tokens = 0
    output_tokens = 0
    found = False
    for line in joined.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        usage = obj.get("usage") or (obj.get("message") or {}).get("usage") or {}
        if usage:
            input_tokens = int(usage.get("input_tokens", input_tokens))
            output_tokens = int(usage.get("output_tokens", output_tokens))
            found = True
    return (input_tokens, output_tokens) if found else None


def _maybe_evict(
    config: Config,
    project_id: str,
    inbound_payload: dict,
    assistant_text: str | None,
    usage: tuple[int, int],
) -> None:
    input_tokens, output_tokens = usage
    fill_ratio = (input_tokens + output_tokens) / config.window_max
    if fill_ratio < config.watermark:
        return

    adapter = AnthropicAdapter()
    conv = adapter.parse(inbound_payload)
    if not conv.turns:
        return

    new_user_tokens = conv.turns[-1].token_count if conv.turns else 0
    new_assistant_tokens = count_tokens(assistant_text or "")
    tokens_to_free = new_user_tokens + new_assistant_tokens
    if tokens_to_free <= 0:
        return

    active = [
        ActiveTurn(
            index=t.source_index if t.source_index is not None else i,
            token_count=t.token_count,
            role=t.role,
            pinned=False,
            memory_type=None,
            is_system=False,
        )
        for i, t in enumerate(conv.turns)
    ]
    result = select_for_eviction(active, tokens_to_free=tokens_to_free, recent_buffer=4)
    if not result.evicted_indexes:
        return

    db = open_project_db(config.db_root, project_id)
    try:
        ts = int(time.time() * 1000)
        episode_ids: list[str] = []
        for idx in result.evicted_indexes:
            turn = next(t for t in conv.turns if (t.source_index or 0) == idx)
            eid = archive_raw(
                db,
                Turn(
                    project_id=project_id,
                    role=turn.role,
                    content=turn.content,
                    tool_calls=turn.tool_calls,
                    code_refs=[],
                    token_count=turn.token_count,
                    ts=ts,
                ),
            )
            episode_ids.append(eid)
        if episode_ids:
            placeholders = ",".join("?" for _ in episode_ids)
            db.execute(
                f"UPDATE episodes SET evicted=1 WHERE id IN ({placeholders})",
                episode_ids,
            )
    finally:
        db.close()


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="spillover", version="0.1.0")
    app.add_middleware(ProjectIdMiddleware)
    app.state.config = config
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    @app.on_event("shutdown")
    async def _close():
        await app.state.http_client.aclose()

    @app.post("/v1/messages")
    async def messages(request: Request):
        body = await request.body()
        payload = json.loads(body)
        project_id = request.state.project_id
        upstream_url = f"{config.upstream_base_url}/v1/messages"
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in {"host", "content-length", "x-project"}
        }
        is_stream = bool(payload.get("stream"))

        if not is_stream:
            r = await app.state.http_client.post(
                upstream_url, headers=fwd_headers, content=body
            )
            resp_body = r.content
            usage = _extract_usage_non_streaming(resp_body)
            if usage is not None:
                resp_json = r.json()
                assistant_text = "".join(
                    b.get("text", "")
                    for b in resp_json.get("content", [])
                    if isinstance(b, dict)
                )
                _maybe_evict(config, project_id, payload, assistant_text, usage)
            return JSONResponse(
                content=r.json(),
                status_code=r.status_code,
                headers={"content-type": "application/json"},
            )

        async def proxy_stream():
            sink: list[bytes] = []
            async with app.state.http_client.stream(
                "POST", upstream_url, headers=fwd_headers, content=body
            ) as r:
                async for chunk in duplicate_stream(r.aiter_bytes(), sink):
                    yield chunk
            usage = _extract_usage_sse(sink)
            if usage is not None:
                # Best-effort text extraction from deltas
                joined = b"".join(sink).decode("utf-8", errors="replace")
                assistant_text = ""
                for line in joined.splitlines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        obj = json.loads(line[len("data:") :].strip())
                    except Exception:
                        continue
                    delta = obj.get("delta") or {}
                    if "text" in delta:
                        assistant_text += delta["text"]
                _maybe_evict(config, project_id, payload, assistant_text, usage)

        return StreamingResponse(proxy_stream(), media_type="text/event-stream")

    return app
```

Note: `_maybe_evict` collects the episode ids returned by `archive_raw` and marks them `evicted=1` in a single batched UPDATE. Plan 2 extends this to also enqueue facet-extraction work.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_eviction_lifecycle.py -v`
Expected: 2 PASSED.

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/spillover/proxy/app.py tests/integration/test_eviction_lifecycle.py
git commit -m "feat(proxy): wire eviction selector + archive writer post-response"
```

---

## Phase 7 — CLI surface

### Task 14: `spillover up` and `spillover stats <project>`

**Files:**
- Create: `src/spillover/cli.py`
- Create: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_cli.py`:

```python
import time

from click.testing import CliRunner

from spillover.archive.writer import Turn, archive_raw
from spillover.cli import main
from spillover.storage.sqlite import open_project_db


def test_stats_empty_project(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(main, ["stats", "nonexistent"])
    assert result.exit_code == 0
    assert "episodes: 0" in result.output


def test_stats_with_episodes(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    db = open_project_db(tmp_path, "p1")
    try:
        for i in range(3):
            archive_raw(
                db,
                Turn(
                    project_id="p1",
                    role="user",
                    content=f"msg {i}",
                    tool_calls=[],
                    code_refs=[],
                    token_count=10,
                    ts=int(time.time() * 1000) + i,
                ),
            )
        db.execute("UPDATE episodes SET evicted=1")
    finally:
        db.close()

    runner = CliRunner()
    result = runner.invoke(main, ["stats", "p1"])
    assert result.exit_code == 0
    assert "episodes: 3" in result.output
    assert "evicted: 3" in result.output


def test_up_shows_help_for_now(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(main, ["up", "--help"])
    assert result.exit_code == 0
    assert "Start the spillover proxy" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `cli.py`**

`src/spillover/cli.py`:

```python
from __future__ import annotations

import click
import uvicorn

from spillover.config import Config
from spillover.proxy.app import create_app
from spillover.storage.sqlite import open_project_db, project_db_path


@click.group()
def main():
    """spillover — transparent LLM proxy with overflow memory."""


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_cli.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Smoke test the CLI**

Run: `spillover --help`
Expected: shows `up` and `stats` subcommands.

Run: `spillover stats anyproject`
Expected: `project anyproject: episodes: 0`.

- [ ] **Step 6: Commit**

```bash
git add src/spillover/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): spillover up + stats subcommands"
```

---

## Phase 8 — Final verification

### Task 15: Full test suite + manual smoke

**Files:** none new.

- [ ] **Step 1: Run full suite**

Run: `pytest -v`
Expected: all tests pass. Confirm at least these test files ran:
- `tests/unit/test_config.py`
- `tests/unit/test_storage_sqlite.py`
- `tests/unit/test_tokenizer.py`
- `tests/unit/test_archive_writer.py`
- `tests/unit/test_eviction_selector.py`
- `tests/unit/test_adapter_base.py`
- `tests/unit/test_adapter_anthropic.py`
- `tests/unit/test_middleware.py`
- `tests/unit/test_streaming.py`
- `tests/unit/test_cli.py`
- `tests/integration/test_proxy_passthrough.py`
- `tests/integration/test_eviction_lifecycle.py`

- [ ] **Step 2: Lint check**

Run: `ruff check src/ tests/`
Expected: 0 errors. If any, fix inline.

- [ ] **Step 3: Manual smoke (optional, requires real Anthropic key)**

In one terminal:

```bash
spillover up
```

In another:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 \
ANTHROPIC_API_KEY=$REAL_KEY \
python -c "
import anthropic, os
c = anthropic.Anthropic(
    base_url=os.environ['ANTHROPIC_BASE_URL'],
    default_headers={'X-Project': '/tmp/test-project'},
)
r = c.messages.create(
    model='claude-haiku-4-5-20251001',
    max_tokens=100,
    messages=[{'role':'user','content':'reply with the single word: hello'}],
)
print(r.content[0].text)
"
```

Then:

```bash
spillover stats $(python -c "import hashlib; print(hashlib.sha1(b'/tmp/test-project').hexdigest())")
```

Expected: at least one episode if you sent enough tokens; otherwise `episodes: 0`.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit --allow-empty -m "chore: spillover MVP foundation complete (Plan 1 done)"
```

- [ ] **Step 5: Tag the release**

```bash
git tag -a v0.1.0 -m "spillover v0.1.0 — MVP foundation (Plan 1)"
```

---

## Definition of done

This plan is complete when ALL of the following are true:

1. `pytest -v` exits 0 with at least 25 passing tests across the listed files.
2. `ruff check src/ tests/` exits 0.
3. `spillover up` starts a proxy on the configured port.
4. `spillover stats <project>` reports correct counts.
5. The token-balance invariant test (`test_token_balance_invariant_over_50_turns`) passes.
6. End-to-end integration tests prove eviction triggers above watermark and does not below.
7. All code committed with conventional-commit messages.
8. `v0.1.0` tag exists.

## What unlocks next

Once this plan is done and validated:

- **Plan 2 (retriever):** adds sqlite-vec + Kuzu, facet pipeline, hybrid retrieval, LTM injection.
- **Plan 3 (counter-compaction):** adds usage rewrite, env-var disable, conversation diff detection, rescue.
- **Plan 4 (multi-CLI + polish):** OpenAI adapter, decay scheduler, observability, wrappers, A/B benchmark.

End of plan.
