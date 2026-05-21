# spillover Retriever Implementation Plan (Plan 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the retrieval half of spillover on top of Plan 1's foundation: a facet-extraction pipeline that turns each archived episode into an embedding + entity graph, a hybrid retriever that fuses vector similarity with graph-walk results, and an LTM injection step in the proxy that prepends the top-K relevant past episodes to the outbound prompt under a configurable token budget.

**Architecture:** Plan 1 left every archived turn raw, with `facet_pending=1` and `memory_type=NULL`. Plan 2 plugs an `asyncio.Queue`-based background worker into the proxy: when `archive_raw` finishes, the proxy enqueues `FacetExtractEvent(episode_id, project_id)`. The worker pops events, runs fastembed to produce a 768-dim embedding, runs simple regex/NER to extract entities + decisions + code_refs, classifies the memory type, then writes one row to `vec_episodes` (sqlite-vec) and one or more nodes/edges in Kuzu. On the proxy's hot path BEFORE forwarding the request to Anthropic, a retriever runs a hybrid query (vector top-K from sqlite-vec + k-hop walk from Kuzu seeded by entities mentioned in the active context), fuses the rankings via Reciprocal Rank Fusion with type-weights, trims to the LTM token budget, renders the chosen episodes as a single `<spillover-ltm>` block, and prepends it to the inbound payload's `system` field before the adapter forwards upstream. No counter-compaction defenses yet (Plan 3) and no OpenAI adapter yet (Plan 4).

**Tech Stack (additions on top of Plan 1):**
- `sqlite-vec >= 0.1.6` — SQLite extension for vector similarity (loaded into the same per-project DB)
- `kuzu >= 0.8.0` — embedded graph DB (per-project directory under `~/.spillover/projects/<pid>/kuzu/`)
- `fastembed >= 0.4` — quantized ONNX embeddings (model `nomic-ai/nomic-embed-text-v1.5-Q`, ~130 MB, fetched on first use into the local fastembed cache)
- everything else stays the same as Plan 1

**Scope NOT covered in this plan (deferred to Plan 3 / Plan 4):**
- Counter-compaction defenses (usage rewrite, env-var disable, intercept, conversation-diff rescue)
- OpenAI adapter
- Decay scheduler / `importance` cron
- Re-hit promotion (`hit_count` increments)
- Pinning CLI commands
- A/B benchmark
- Wrappers for other CLIs

End state of this plan:
- Each archived episode has a row in `vec_episodes` and corresponding nodes/edges in Kuzu within seconds of being archived.
- The proxy injects an LTM block into the outbound payload when relevant past episodes exist.
- All Plan 1 tests still pass; ~25 new tests cover the new code.
- `spillover stats` reports facet-pipeline depth and embedding counts.
- A new `spillover query <project> "<text>"` command runs the retriever ad-hoc and prints the ranked list.

---

## File structure

New files:

```
src/spillover/
  facet/
    __init__.py                # NEW
    worker.py                  # NEW (asyncio queue consumer)
    embed.py                   # NEW (fastembed wrapper)
    entities.py                # NEW (regex + simple NER)
    decisions.py               # NEW (decision/code-ref parsers)
    classifier.py              # NEW (procedural/episodic/semantic/priority)
  retriever/
    __init__.py                # NEW
    vector.py                  # NEW (sqlite-vec query)
    graph.py                   # NEW (Kuzu k-hop query)
    fusion.py                  # NEW (RRF with type weights)
    budget.py                  # NEW (token-budget trim)
    render.py                  # NEW (compose <spillover-ltm> block)
  storage/
    vec_schema.sql             # NEW (vec_episodes virtual table)
    kuzu_schema.cypher         # NEW (node/edge definitions)
    kuzu.py                    # NEW (per-project Kuzu factory)

tests/unit/
  test_facet_embed.py          # NEW
  test_facet_entities.py       # NEW
  test_facet_decisions.py      # NEW
  test_facet_classifier.py     # NEW
  test_facet_worker.py         # NEW
  test_retriever_vector.py     # NEW
  test_retriever_graph.py      # NEW
  test_retriever_fusion.py     # NEW
  test_retriever_budget.py     # NEW
  test_retriever_render.py     # NEW
  test_storage_kuzu.py         # NEW
tests/integration/
  test_retriever_lifecycle.py  # NEW (end-to-end: archive → facet → retrieve → inject)
```

Modified files:

```
src/spillover/
  proxy/app.py                 # MODIFIED (enqueue facet event after archive, inject LTM before forward)
  storage/sqlite.py            # MODIFIED (load sqlite-vec extension, run vec_schema.sql)
  storage/schema.sql           # MODIFIED (no change at the table level; vec_schema is separate)
  config.py                    # MODIFIED (add embed_model, ltm_budget_pct, retriever_topk, kuzu_root)
  cli.py                       # MODIFIED (add `spillover query`, extend `spillover stats` to show facet-queue depth)
pyproject.toml                 # MODIFIED (add sqlite-vec, kuzu, fastembed deps)
```

Single responsibility per new file:
- `facet/worker.py` — pulls from in-process `asyncio.Queue`, orchestrates the facet pipeline. Does not embed or write directly; calls into the other facet modules.
- `facet/embed.py` — one function `embed_text(text) -> list[float]` backed by fastembed, with model load lazily on first call.
- `facet/entities.py` — `extract_entities(text) -> list[Entity]` with regex (file paths, urls, code identifiers) + an optional spaCy-lite fallback. No DB writes.
- `facet/decisions.py` — `extract_decisions(text) -> list[Decision]`, `extract_code_refs(tool_calls) -> list[CodeRef]`. Pure parsers.
- `facet/classifier.py` — `classify(content, tool_calls) -> Literal["procedural","episodic","semantic","priority"]`. Heuristic, no LLM call in this plan.
- `retriever/vector.py` — `vector_topk(db, embedding, k) -> list[Hit]`.
- `retriever/graph.py` — `graph_walk(kuzu_conn, seed_entities, k_hop, limit) -> list[Hit]`.
- `retriever/fusion.py` — `rrf_fuse(vector_hits, graph_hits, type_weights) -> list[Hit]`.
- `retriever/budget.py` — `trim_to_budget(hits, max_tokens) -> list[Hit]`.
- `retriever/render.py` — `render_ltm_block(hits) -> str` (markdown wrapped in `<spillover-ltm>` tag).
- `storage/kuzu.py` — `open_project_kuzu(db_root, project_id) -> kuzu.Connection`.
- `storage/vec_schema.sql` / `kuzu_schema.cypher` — DDL.

---

## Phase 0 — Dependencies + storage layer additions

### Task 1: Add deps + verify sqlite-vec loads

**Files:**
- Modify: `pyproject.toml`
- Create: `src/spillover/storage/vec_schema.sql`
- Modify: `src/spillover/storage/sqlite.py`
- Create: `tests/unit/test_storage_vec.py`

- [ ] **Step 1: Add deps to pyproject.toml**

Add to the `dependencies` list (between `pydantic>=2.6` and the closing `]`):

```toml
    "sqlite-vec>=0.1.6",
    "kuzu>=0.8.0",
    "fastembed>=0.4",
```

Run `python -m pip install -e ".[dev]"`. Confirm all three install (sqlite-vec is a small wheel; kuzu ships its native lib; fastembed pulls onnxruntime). On Windows, fastembed may pull a CPU-only onnxruntime — that's fine.

- [ ] **Step 2: Write vec_schema.sql**

`src/spillover/storage/vec_schema.sql`:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes USING vec0(
    episode_id TEXT PRIMARY KEY,
    embedding FLOAT[768],
    memory_type TEXT,
    importance FLOAT,
    ts INTEGER
);
```

- [ ] **Step 3: Modify sqlite.py to load extension + apply vec schema**

Replace `open_project_db` with:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_VEC_SCHEMA_PATH = Path(__file__).with_name("vec_schema.sql")


def project_db_path(db_root: Path, project_id: str) -> Path:
    return db_root / "projects" / project_id / "episodes.db"


def open_project_db(db_root: Path, project_id: str) -> sqlite3.Connection:
    path = project_db_path(db_root, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(_VEC_SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn
```

- [ ] **Step 4: Write failing test**

`tests/unit/test_storage_vec.py`:

```python
from spillover.storage.sqlite import open_project_db


def test_vec_episodes_table_exists(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        tables = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        }
        assert "vec_episodes" in tables
    finally:
        db.close()


def test_vec_episodes_accepts_insert_and_query(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        emb = [0.1] * 768
        db.execute(
            "INSERT INTO vec_episodes(episode_id, embedding, memory_type, importance, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            ("e1", bytes_from_floats(emb), "episodic", 1.0, 0),
        )
        rows = db.execute(
            "SELECT episode_id FROM vec_episodes WHERE episode_id = ?", ("e1",)
        ).fetchall()
        assert len(rows) == 1
    finally:
        db.close()


def bytes_from_floats(floats):
    import struct

    return struct.pack(f"<{len(floats)}f", *floats)
```

- [ ] **Step 5: Run test**

Run: `python -m pytest tests/unit/test_storage_vec.py -v`
Expected: 2 PASSED.

- [ ] **Step 6: Run full suite to confirm no regression**

Run: `python -m pytest -v`
Expected: 55 PASSED (53 from Plan 1 + 2 new).

- [ ] **Step 7: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(storage): load sqlite-vec extension + vec_episodes virtual table"
```

---

### Task 2: Kuzu schema + per-project connection factory

**Files:**
- Create: `src/spillover/storage/kuzu_schema.cypher`
- Create: `src/spillover/storage/kuzu.py`
- Create: `tests/unit/test_storage_kuzu.py`

- [ ] **Step 1: Write `kuzu_schema.cypher`**

`src/spillover/storage/kuzu_schema.cypher`:

```cypher
CREATE NODE TABLE IF NOT EXISTS Episode (
    id STRING PRIMARY KEY,
    ts INT64,
    memory_type STRING,
    importance DOUBLE
);

CREATE NODE TABLE IF NOT EXISTS Entity (
    name STRING PRIMARY KEY,
    kind STRING
);

CREATE NODE TABLE IF NOT EXISTS File (
    path STRING PRIMARY KEY,
    ext STRING
);

CREATE NODE TABLE IF NOT EXISTS Decision (
    hash STRING PRIMARY KEY,
    summary STRING
);

CREATE NODE TABLE IF NOT EXISTS Command (
    sig STRING PRIMARY KEY,
    first_seen_ts INT64
);

CREATE REL TABLE IF NOT EXISTS MENTIONS (FROM Episode TO Entity);
CREATE REL TABLE IF NOT EXISTS TOUCHED (FROM Episode TO File);
CREATE REL TABLE IF NOT EXISTS IMPLEMENTS (FROM Episode TO Decision);
CREATE REL TABLE IF NOT EXISTS RAN (FROM Episode TO Command);
CREATE REL TABLE IF NOT EXISTS AFTER (FROM Episode TO Episode);
```

- [ ] **Step 2: Write `kuzu.py`**

`src/spillover/storage/kuzu.py`:

```python
from __future__ import annotations

from pathlib import Path

import kuzu

_SCHEMA_PATH = Path(__file__).with_name("kuzu_schema.cypher")


def project_kuzu_dir(db_root: Path, project_id: str) -> Path:
    return db_root / "projects" / project_id / "kuzu"


def open_project_kuzu(db_root: Path, project_id: str) -> kuzu.Connection:
    """Open or create the per-project Kuzu graph DB and ensure schema is applied."""
    path = project_kuzu_dir(db_root, project_id)
    path.mkdir(parents=True, exist_ok=True)
    db = kuzu.Database(str(path))
    conn = kuzu.Connection(db)
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    for statement in [s.strip() for s in schema.split(";") if s.strip()]:
        conn.execute(statement)
    return conn
```

- [ ] **Step 3: Write failing test**

`tests/unit/test_storage_kuzu.py`:

```python
from spillover.storage.kuzu import open_project_kuzu, project_kuzu_dir


def test_kuzu_dir_path(tmp_path):
    p = project_kuzu_dir(tmp_path, "p1")
    assert p == tmp_path / "projects" / "p1" / "kuzu"


def test_open_creates_dir_and_schema(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    # MERGE then query Episode node
    conn.execute("CREATE (e:Episode {id: 'e1', ts: 0, memory_type: 'episodic', importance: 1.0})")
    result = conn.execute("MATCH (e:Episode {id: 'e1'}) RETURN e.id")
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    assert rows[0][0] == "e1"


def test_open_idempotent(tmp_path):
    open_project_kuzu(tmp_path, "p1")
    conn = open_project_kuzu(tmp_path, "p1")
    # Schema already there, no error
    result = conn.execute("MATCH (e:Episode) RETURN count(e)")
    while result.has_next():
        result.get_next()
```

- [ ] **Step 4: Run test**

Run: `python -m pytest tests/unit/test_storage_kuzu.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(storage): per-project Kuzu graph DB with schema"
```

---

## Phase 1 — Facet extraction primitives

### Task 3: Embedder via fastembed

**Files:**
- Create: `src/spillover/facet/embed.py`
- Create: `tests/unit/test_facet_embed.py`

- [ ] **Step 1: Write `embed.py`**

`src/spillover/facet/embed.py`:

```python
from __future__ import annotations

from functools import lru_cache

from fastembed import TextEmbedding

_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5-Q"
EMBED_DIM = 768


@lru_cache(maxsize=1)
def _embedder() -> TextEmbedding:
    return TextEmbedding(model_name=_MODEL_NAME)


def embed_text(text: str) -> list[float]:
    """Embed a single string. Returns a 768-dim float list."""
    if not text:
        return [0.0] * EMBED_DIM
    vectors = list(_embedder().embed([text]))
    return list(vectors[0].tolist())
```

- [ ] **Step 2: Write test**

`tests/unit/test_facet_embed.py`:

```python
import pytest

from spillover.facet.embed import EMBED_DIM, embed_text


@pytest.mark.slow
def test_embed_text_returns_correct_dim():
    v = embed_text("hello world")
    assert len(v) == EMBED_DIM
    assert all(isinstance(x, float) for x in v)


@pytest.mark.slow
def test_embed_text_deterministic():
    v1 = embed_text("the quick brown fox")
    v2 = embed_text("the quick brown fox")
    assert v1 == v2


def test_embed_text_empty():
    v = embed_text("")
    assert v == [0.0] * EMBED_DIM
```

The first two tests are marked `@pytest.mark.slow` because they trigger the ~130MB fastembed model download on first run. Register the marker in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-ra -q"
markers = [
    "slow: tests that download the embedding model (~130MB)",
]
```

By default `pytest -v` runs everything. To skip slow tests use `pytest -v -m "not slow"`. CI runs them; local dev can opt out.

- [ ] **Step 3: Run**

Run: `python -m pytest tests/unit/test_facet_embed.py -v`
Expected: 3 PASSED (first invocation downloads the model — takes ~30s; subsequent runs hit the cache).

- [ ] **Step 4: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(facet): fastembed-backed embed_text (nomic-embed-text-v1.5-Q)"
```

---

### Task 4: Entity extraction (regex + simple types)

**Files:**
- Create: `src/spillover/facet/entities.py`
- Create: `tests/unit/test_facet_entities.py`

- [ ] **Step 1: Write `entities.py`**

`src/spillover/facet/entities.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Entity:
    name: str
    kind: str  # "file" | "url" | "identifier" | "command"


_FILE_PATH = re.compile(
    r"(?<![A-Za-z0-9])"
    r"((?:[A-Za-z]:[\\/])?(?:[\w.\-]+[\\/])+[\w.\-]+\.\w+)"
)
_URL = re.compile(r"https?://[^\s)>\"]+")
_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Za-z0-9]+|[a-z_][a-z0-9_]+)(?=\()")
_COMMAND = re.compile(r"`([a-z][a-z0-9_\- ]{1,40})`")


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content", "")))
        return "\n".join(parts)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
    return ""


def extract_entities(content: Any) -> list[Entity]:
    text = _content_to_text(content)
    seen: set[tuple[str, str]] = set()
    out: list[Entity] = []
    for m in _FILE_PATH.finditer(text):
        key = (m.group(1), "file")
        if key not in seen:
            seen.add(key)
            out.append(Entity(name=m.group(1), kind="file"))
    for m in _URL.finditer(text):
        key = (m.group(0), "url")
        if key not in seen:
            seen.add(key)
            out.append(Entity(name=m.group(0), kind="url"))
    for m in _IDENTIFIER.finditer(text):
        key = (m.group(1), "identifier")
        if key not in seen:
            seen.add(key)
            out.append(Entity(name=m.group(1), kind="identifier"))
    for m in _COMMAND.finditer(text):
        key = (m.group(1).strip(), "command")
        if key not in seen:
            seen.add(key)
            out.append(Entity(name=m.group(1).strip(), kind="command"))
    return out
```

- [ ] **Step 2: Write test**

`tests/unit/test_facet_entities.py`:

```python
from spillover.facet.entities import Entity, extract_entities


def test_extracts_file_path():
    entities = extract_entities("see src/spillover/proxy/app.py line 42")
    files = [e for e in entities if e.kind == "file"]
    assert any(e.name == "src/spillover/proxy/app.py" for e in files)


def test_extracts_url():
    entities = extract_entities("docs at https://example.com/api ok")
    urls = [e for e in entities if e.kind == "url"]
    assert any(e.name == "https://example.com/api" for e in urls)


def test_extracts_identifier_called():
    entities = extract_entities("we call processBatch() then commit()")
    idents = [e for e in entities if e.kind == "identifier"]
    names = {e.name for e in idents}
    assert "processBatch" in names
    assert "commit" in names


def test_extracts_command_in_backticks():
    entities = extract_entities("run `git status` to check")
    cmds = [e for e in entities if e.kind == "command"]
    assert any(e.name == "git status" for e in cmds)


def test_dedup_repeated_entities():
    entities = extract_entities("foo.py and foo.py again")
    files = [e for e in entities if e.kind == "file"]
    assert len(files) == 1


def test_handles_list_content():
    content = [
        {"type": "text", "text": "see /tmp/x.log"},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/y.log"}},
    ]
    entities = extract_entities(content)
    files = [e.name for e in entities if e.kind == "file"]
    assert "/tmp/x.log" in files


def test_empty_returns_empty():
    assert extract_entities("") == []
    assert extract_entities([]) == []
    assert extract_entities(None) == []
```

- [ ] **Step 3: Run**

Run: `python -m pytest tests/unit/test_facet_entities.py -v`
Expected: 7 PASSED.

- [ ] **Step 4: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(facet): regex-based entity extractor (file/url/identifier/command)"
```

---

### Task 5: Decisions + code_refs parsers

**Files:**
- Create: `src/spillover/facet/decisions.py`
- Create: `tests/unit/test_facet_decisions.py`

- [ ] **Step 1: Write `decisions.py`**

`src/spillover/facet/decisions.py`:

```python
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Decision:
    hash: str
    summary: str


@dataclass(frozen=True)
class CodeRef:
    path: str
    line: int | None = None
    op: str | None = None  # "read" | "write" | "edit" | "run"


_DECISION_PT = re.compile(
    r"(?im)^(?:.*?\b(decidi|escolhi|abandonei|optei|preferi)\b.{1,200})$"
)
_DECISION_EN = re.compile(
    r"(?im)^(?:.*?\b(decided|chose|abandoned|opted|picked|going with)\b.{1,200})$"
)

_BECAUSE = re.compile(
    r"(?im)\b(porque|pq|because|reason|motivo)\b[:\s].{1,200}"
)


def _summary(line: str) -> str:
    s = line.strip()
    if len(s) > 200:
        s = s[:200] + "..."
    return s


def _hash_summary(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def extract_decisions(content: Any) -> list[Decision]:
    text = _content_to_text(content)
    seen: set[str] = set()
    out: list[Decision] = []
    for regex in (_DECISION_PT, _DECISION_EN, _BECAUSE):
        for m in regex.finditer(text):
            summary = _summary(m.group(0))
            h = _hash_summary(summary)
            if h in seen:
                continue
            seen.add(h)
            out.append(Decision(hash=h, summary=summary))
    return out


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        return "\n".join(parts)
    return ""


_TOOL_TO_OP = {
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "Bash": "run",
    "PowerShell": "run",
}


def extract_code_refs(tool_calls: list[dict]) -> list[CodeRef]:
    out: list[CodeRef] = []
    seen: set[tuple[str, int | None, str | None]] = set()
    for call in tool_calls or []:
        name = call.get("name")
        inp = call.get("input") or {}
        op = _TOOL_TO_OP.get(name)
        path = inp.get("file_path") or inp.get("path")
        if path is None:
            continue
        key = (path, None, op)
        if key in seen:
            continue
        seen.add(key)
        out.append(CodeRef(path=path, line=None, op=op))
    return out
```

- [ ] **Step 2: Write test**

`tests/unit/test_facet_decisions.py`:

```python
from spillover.facet.decisions import (
    CodeRef,
    Decision,
    extract_code_refs,
    extract_decisions,
)


def test_extract_decision_ptbr():
    decisions = extract_decisions("decidi usar SQLite em vez de Postgres porque é local")
    assert len(decisions) >= 1
    assert any("decidi" in d.summary.lower() for d in decisions)


def test_extract_decision_en():
    decisions = extract_decisions("We chose Anthropic over OpenAI for prompt caching")
    assert any("chose" in d.summary.lower() for d in decisions)


def test_extract_decision_because():
    decisions = extract_decisions(
        "Switched the watermark because the old value caused thrashing."
    )
    assert any("because" in d.summary.lower() for d in decisions)


def test_decisions_dedup_by_hash():
    text = "decidi X\ndecidi X"
    decisions = extract_decisions(text)
    assert len(decisions) == 1


def test_extract_code_refs_read():
    refs = extract_code_refs(
        [
            {"name": "Read", "input": {"file_path": "/tmp/x.txt"}},
            {"name": "Edit", "input": {"file_path": "/tmp/y.py"}},
        ]
    )
    assert CodeRef(path="/tmp/x.txt", line=None, op="read") in refs
    assert CodeRef(path="/tmp/y.py", line=None, op="edit") in refs


def test_extract_code_refs_dedup():
    refs = extract_code_refs(
        [
            {"name": "Read", "input": {"file_path": "/tmp/x.txt"}},
            {"name": "Read", "input": {"file_path": "/tmp/x.txt"}},
        ]
    )
    assert len(refs) == 1


def test_extract_code_refs_empty():
    assert extract_code_refs([]) == []
    assert extract_code_refs([{"name": "Unknown"}]) == []
```

- [ ] **Step 3: Run**

Run: `python -m pytest tests/unit/test_facet_decisions.py -v`
Expected: 7 PASSED.

- [ ] **Step 4: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(facet): decision + code-ref parsers (regex, dedup, PT-BR + EN)"
```

---

### Task 6: Classifier

**Files:**
- Create: `src/spillover/facet/classifier.py`
- Create: `tests/unit/test_facet_classifier.py`

- [ ] **Step 1: Write `classifier.py`**

`src/spillover/facet/classifier.py`:

```python
from __future__ import annotations

import re
from typing import Any, Literal

MemoryType = Literal["procedural", "episodic", "semantic", "priority"]

_PRIORITY_MARKERS = re.compile(
    r"(?i)\b(remember this|lembra disso|important|importante|never|nunca|always|sempre)\b"
)
_PROCEDURAL_MARKERS = re.compile(
    r"(?i)\b(step \d|first .* then|how to|run the|execute|invoke|call .*\(\))"
)
_SEMANTIC_MARKERS = re.compile(
    r"(?i)\b(is a|are a kind of|definition|convention|architecture|design choice)\b"
)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def classify(content: Any, tool_calls: list[dict] | None = None) -> MemoryType:
    text = _content_to_text(content)
    has_tools = bool(tool_calls)

    if _PRIORITY_MARKERS.search(text):
        return "priority"
    if has_tools or _PROCEDURAL_MARKERS.search(text):
        return "procedural"
    if _SEMANTIC_MARKERS.search(text):
        return "semantic"
    return "episodic"
```

- [ ] **Step 2: Write test**

`tests/unit/test_facet_classifier.py`:

```python
from spillover.facet.classifier import classify


def test_priority_marker():
    assert classify("Remember this: always use uuid7") == "priority"
    assert classify("Lembra disso: nunca commitar segredos") == "priority"


def test_procedural_by_tool_calls():
    assert classify("anything", [{"name": "Read"}]) == "procedural"


def test_procedural_by_marker():
    assert classify("First read the config, then call setup()") == "procedural"


def test_semantic_marker():
    assert classify("A vector index is a kind of approximate nearest neighbor structure") == "semantic"


def test_default_episodic():
    assert classify("We tried it and it worked fine.") == "episodic"


def test_priority_wins_over_procedural():
    """Priority is the strongest signal."""
    assert (
        classify("Remember this: how to deploy", [{"name": "Bash"}])
        == "priority"
    )
```

- [ ] **Step 3: Run**

Run: `python -m pytest tests/unit/test_facet_classifier.py -v`
Expected: 6 PASSED.

- [ ] **Step 4: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(facet): heuristic classifier (priority/procedural/semantic/episodic)"
```

---

## Phase 2 — Facet worker

### Task 7: Async queue worker

**Files:**
- Create: `src/spillover/facet/worker.py`
- Create: `tests/unit/test_facet_worker.py`

- [ ] **Step 1: Write `worker.py`**

`src/spillover/facet/worker.py`:

```python
from __future__ import annotations

import asyncio
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from spillover.facet.classifier import classify
from spillover.facet.decisions import extract_code_refs, extract_decisions
from spillover.facet.embed import EMBED_DIM, embed_text
from spillover.facet.entities import extract_entities
from spillover.logging import get_logger
from spillover.storage.kuzu import open_project_kuzu
from spillover.storage.sqlite import open_project_db

log = get_logger("facet")


@dataclass
class FacetEvent:
    project_id: str
    episode_id: str
    db_root: Path


def _floats_to_bytes(v: list[float]) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def _process_one(event: FacetEvent) -> None:
    db = open_project_db(event.db_root, event.project_id)
    try:
        row = db.execute(
            "SELECT role, content_json, tool_calls_json, ts FROM episodes WHERE id = ?",
            (event.episode_id,),
        ).fetchone()
        if row is None:
            log.warning("facet: episode missing project=%s id=%s",
                         event.project_id, event.episode_id)
            return

        content = json.loads(row["content_json"])
        tool_calls = json.loads(row["tool_calls_json"] or "[]")
        ts = int(row["ts"])

        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        vec = embed_text(text)
        memory_type = classify(content, tool_calls)
        importance = _base_importance(memory_type, len(tool_calls))

        db.execute(
            "INSERT OR REPLACE INTO vec_episodes(episode_id, embedding, memory_type, importance, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (event.episode_id, _floats_to_bytes(vec), memory_type, importance, ts),
        )
        db.execute(
            "UPDATE episodes SET memory_type=?, facet_pending=0 WHERE id=?",
            (memory_type, event.episode_id),
        )
    finally:
        db.close()

    kuzu_conn = open_project_kuzu(event.db_root, event.project_id)
    kuzu_conn.execute(
        "MERGE (e:Episode {id: $id}) SET e.ts = $ts, e.memory_type = $mt, e.importance = $imp",
        {"id": event.episode_id, "ts": ts, "mt": memory_type, "imp": importance},
    )
    for ent in extract_entities(content):
        kuzu_conn.execute(
            "MERGE (n:Entity {name: $name}) SET n.kind = $kind",
            {"name": ent.name, "kind": ent.kind},
        )
        kuzu_conn.execute(
            "MATCH (e:Episode {id: $eid}), (n:Entity {name: $name}) "
            "MERGE (e)-[:MENTIONS]->(n)",
            {"eid": event.episode_id, "name": ent.name},
        )
    for ref in extract_code_refs(tool_calls):
        ext = ref.path.rsplit(".", 1)[-1] if "." in ref.path else ""
        kuzu_conn.execute(
            "MERGE (f:File {path: $path}) SET f.ext = $ext",
            {"path": ref.path, "ext": ext},
        )
        kuzu_conn.execute(
            "MATCH (e:Episode {id: $eid}), (f:File {path: $path}) "
            "MERGE (e)-[:TOUCHED]->(f)",
            {"eid": event.episode_id, "path": ref.path},
        )
    for dec in extract_decisions(content):
        kuzu_conn.execute(
            "MERGE (d:Decision {hash: $h}) SET d.summary = $s",
            {"h": dec.hash, "s": dec.summary},
        )
        kuzu_conn.execute(
            "MATCH (e:Episode {id: $eid}), (d:Decision {hash: $h}) "
            "MERGE (e)-[:IMPLEMENTS]->(d)",
            {"eid": event.episode_id, "h": dec.hash},
        )

    log.info("facet: processed project=%s id=%s type=%s",
              event.project_id, event.episode_id, memory_type)


def _base_importance(memory_type: str, tool_call_count: int) -> float:
    base = {
        "priority": 1.0,
        "procedural": 0.7,
        "semantic": 0.6,
        "episodic": 0.5,
    }[memory_type]
    return min(1.0, base + 0.05 * tool_call_count)


class FacetWorker:
    """Consumes FacetEvent from an asyncio.Queue; runs CPU-bound work in a thread."""

    def __init__(self, queue: asyncio.Queue, *, name: str = "facet-worker"):
        self.queue = queue
        self.name = name
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            event = await self.queue.get()
            try:
                await loop.run_in_executor(None, _process_one, event)
            except Exception:
                log.exception("facet worker error project=%s id=%s",
                              event.project_id, event.episode_id)
            finally:
                self.queue.task_done()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=self.name)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
```

- [ ] **Step 2: Write test**

`tests/unit/test_facet_worker.py`:

```python
import asyncio
import json
import time

import pytest

from spillover.archive.writer import Turn, archive_raw
from spillover.facet.worker import FacetEvent, FacetWorker, _process_one
from spillover.storage.sqlite import open_project_db


@pytest.mark.slow
def test_process_one_writes_vec_and_updates_pending(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="Refactor src/foo.py to use config from env. decidi usar SQLite porque é local.",
                tool_calls=[{"name": "Read", "input": {"file_path": "src/foo.py"}}],
                code_refs=[],
                token_count=20,
                ts=int(time.time() * 1000),
            ),
        )
    finally:
        db.close()

    _process_one(FacetEvent(project_id="p1", episode_id=eid, db_root=tmp_path))

    db = open_project_db(tmp_path, "p1")
    try:
        row = db.execute(
            "SELECT memory_type, facet_pending FROM episodes WHERE id = ?", (eid,)
        ).fetchone()
        assert row["facet_pending"] == 0
        assert row["memory_type"] in {"procedural", "priority", "semantic", "episodic"}
        vec_row = db.execute(
            "SELECT episode_id FROM vec_episodes WHERE episode_id = ?", (eid,)
        ).fetchone()
        assert vec_row is not None
    finally:
        db.close()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_worker_consumes_queue(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="hello",
                tool_calls=[],
                code_refs=[],
                token_count=1,
                ts=0,
            ),
        )
    finally:
        db.close()

    q: asyncio.Queue = asyncio.Queue()
    worker = FacetWorker(q)
    worker.start()
    await q.put(FacetEvent(project_id="p1", episode_id=eid, db_root=tmp_path))
    await q.join()
    await worker.stop()

    db = open_project_db(tmp_path, "p1")
    try:
        row = db.execute(
            "SELECT facet_pending FROM episodes WHERE id = ?", (eid,)
        ).fetchone()
        assert row["facet_pending"] == 0
    finally:
        db.close()
```

- [ ] **Step 3: Run**

Run: `python -m pytest tests/unit/test_facet_worker.py -v`
Expected: 2 PASSED (slow, hits embedder).

- [ ] **Step 4: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(facet): async worker reading queue, writes vec+graph per episode"
```

---

## Phase 3 — Retriever

### Task 8: Vector top-K

**Files:**
- Create: `src/spillover/retriever/__init__.py`
- Create: `src/spillover/retriever/vector.py`
- Create: `tests/unit/test_retriever_vector.py`

- [ ] **Step 1: Write `vector.py`**

`src/spillover/retriever/vector.py`:

```python
from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass


@dataclass
class Hit:
    episode_id: str
    score: float
    memory_type: str | None = None
    importance: float | None = None
    ts: int | None = None
    source: str = "vector"


def _floats_to_bytes(v: list[float]) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def vector_topk(db: sqlite3.Connection, embedding: list[float], k: int = 50) -> list[Hit]:
    rows = db.execute(
        "SELECT episode_id, distance, memory_type, importance, ts "
        "FROM vec_episodes "
        "WHERE embedding MATCH ? "
        "ORDER BY distance LIMIT ?",
        (_floats_to_bytes(embedding), k),
    ).fetchall()
    return [
        Hit(
            episode_id=r["episode_id"],
            score=1.0 - float(r["distance"]),  # convert distance to similarity
            memory_type=r["memory_type"],
            importance=r["importance"],
            ts=r["ts"],
            source="vector",
        )
        for r in rows
    ]
```

- [ ] **Step 2: Write test**

`tests/unit/test_retriever_vector.py`:

```python
import struct

from spillover.retriever.vector import vector_topk
from spillover.storage.sqlite import open_project_db


def _b(v):
    return struct.pack(f"<{len(v)}f", *v)


def test_vector_topk_orders_by_distance(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        # 4 episodes, embeddings designed so that query [1,0,0,...] hits e1 first
        for eid, vec in [
            ("e1", [1.0] + [0.0] * 767),
            ("e2", [0.9] + [0.0] * 767),
            ("e3", [0.0, 1.0] + [0.0] * 766),
            ("e4", [-1.0] + [0.0] * 767),
        ]:
            db.execute(
                "INSERT INTO vec_episodes(episode_id, embedding, memory_type, importance, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (eid, _b(vec), "episodic", 1.0, 0),
            )
        hits = vector_topk(db, [1.0] + [0.0] * 767, k=3)
        ids = [h.episode_id for h in hits]
        assert ids[0] == "e1"
        assert "e4" not in ids
        assert len(hits) == 3
        assert all(h.source == "vector" for h in hits)
    finally:
        db.close()
```

- [ ] **Step 3: Run**

Run: `python -m pytest tests/unit/test_retriever_vector.py -v`
Expected: 1 PASSED.

- [ ] **Step 4: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(retriever): vector_topk via sqlite-vec MATCH"
```

---

### Task 9: Graph k-hop walk

**Files:**
- Create: `src/spillover/retriever/graph.py`
- Create: `tests/unit/test_retriever_graph.py`

- [ ] **Step 1: Write `graph.py`**

`src/spillover/retriever/graph.py`:

```python
from __future__ import annotations

import kuzu

from spillover.retriever.vector import Hit


def graph_walk(
    conn: kuzu.Connection,
    seed_entities: list[str],
    k_hop: int = 2,
    limit: int = 50,
) -> list[Hit]:
    """Return Episode hits reached from seed entities within k_hop edges.

    Score = 1 / hop_distance (1 hop = 1.0, 2 hops = 0.5).
    """
    if not seed_entities:
        return []
    hits: dict[str, float] = {}
    # 1-hop: episodes that directly MENTION any seed entity
    q1 = """
    MATCH (e:Episode)-[:MENTIONS]->(n:Entity)
    WHERE n.name IN $names
    RETURN e.id, e.memory_type, e.importance, e.ts
    LIMIT $limit
    """
    res = conn.execute(q1, {"names": seed_entities, "limit": limit})
    while res.has_next():
        eid, mt, imp, ts = res.get_next()
        hits[eid] = max(hits.get(eid, 0.0), 1.0)

    if k_hop >= 2:
        # 2-hop: episodes mentioning entities mentioned by 1-hop episodes
        q2 = """
        MATCH (e:Episode)-[:MENTIONS]->(n2:Entity)<-[:MENTIONS]-(e2:Episode)-[:MENTIONS]->(n:Entity)
        WHERE n.name IN $names AND e <> e2
        RETURN DISTINCT e.id
        LIMIT $limit
        """
        res = conn.execute(q2, {"names": seed_entities, "limit": limit})
        while res.has_next():
            (eid,) = res.get_next()
            hits[eid] = max(hits.get(eid, 0.0), 0.5)

    # Resolve metadata for each hit
    out: list[Hit] = []
    for eid, score in sorted(hits.items(), key=lambda kv: -kv[1])[:limit]:
        meta = conn.execute(
            "MATCH (e:Episode {id: $id}) RETURN e.memory_type, e.importance, e.ts",
            {"id": eid},
        )
        mt = imp = ts = None
        if meta.has_next():
            mt, imp, ts = meta.get_next()
        out.append(
            Hit(
                episode_id=eid,
                score=score,
                memory_type=mt,
                importance=imp,
                ts=ts,
                source="graph",
            )
        )
    return out
```

- [ ] **Step 2: Write test**

`tests/unit/test_retriever_graph.py`:

```python
from spillover.retriever.graph import graph_walk
from spillover.storage.kuzu import open_project_kuzu


def test_graph_walk_one_hop(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    conn.execute("CREATE (e:Episode {id: 'e1', ts: 0, memory_type: 'episodic', importance: 1.0})")
    conn.execute("CREATE (n:Entity {name: 'foo.py', kind: 'file'})")
    conn.execute(
        "MATCH (e:Episode {id: 'e1'}), (n:Entity {name: 'foo.py'}) "
        "CREATE (e)-[:MENTIONS]->(n)"
    )
    hits = graph_walk(conn, ["foo.py"], k_hop=1, limit=10)
    assert len(hits) == 1
    assert hits[0].episode_id == "e1"
    assert hits[0].score == 1.0
    assert hits[0].source == "graph"


def test_graph_walk_empty_seeds(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    assert graph_walk(conn, [], k_hop=2, limit=10) == []


def test_graph_walk_no_match(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    conn.execute("CREATE (e:Episode {id: 'e1', ts: 0, memory_type: 'episodic', importance: 1.0})")
    hits = graph_walk(conn, ["missing.py"], k_hop=1, limit=10)
    assert hits == []
```

- [ ] **Step 3: Run**

Run: `python -m pytest tests/unit/test_retriever_graph.py -v`
Expected: 3 PASSED.

- [ ] **Step 4: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(retriever): graph_walk via Kuzu k-hop MENTIONS chain"
```

---

### Task 10: RRF fusion

**Files:**
- Create: `src/spillover/retriever/fusion.py`
- Create: `tests/unit/test_retriever_fusion.py`

- [ ] **Step 1: Write `fusion.py`**

`src/spillover/retriever/fusion.py`:

```python
from __future__ import annotations

from spillover.retriever.vector import Hit

DEFAULT_TYPE_WEIGHTS = {
    "priority": 1.5,
    "procedural": 1.2,
    "episodic": 1.0,
    "semantic": 1.0,
}

RRF_K = 60  # standard RRF dampening constant


def rrf_fuse(
    *rankings: list[Hit],
    type_weights: dict[str, float] | None = None,
) -> list[Hit]:
    """Reciprocal Rank Fusion across one or more ranked lists.

    Each ranking is a list of Hit (already in descending score order).
    Returns the merged ranking, in descending fused score, deduplicated by episode_id.
    """
    weights = type_weights or DEFAULT_TYPE_WEIGHTS
    scores: dict[str, float] = {}
    meta: dict[str, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            type_w = weights.get(hit.memory_type or "episodic", 1.0)
            contrib = type_w / (RRF_K + rank)
            scores[hit.episode_id] = scores.get(hit.episode_id, 0.0) + contrib
            if hit.episode_id not in meta:
                meta[hit.episode_id] = hit
    fused: list[Hit] = []
    for eid in sorted(scores, key=lambda k: -scores[k]):
        h = meta[eid]
        fused.append(
            Hit(
                episode_id=eid,
                score=scores[eid],
                memory_type=h.memory_type,
                importance=h.importance,
                ts=h.ts,
                source="fusion",
            )
        )
    return fused
```

- [ ] **Step 2: Write test**

`tests/unit/test_retriever_fusion.py`:

```python
from spillover.retriever.fusion import RRF_K, rrf_fuse
from spillover.retriever.vector import Hit


def test_single_ranking_passthrough():
    r = [Hit("a", 0.9), Hit("b", 0.8)]
    out = rrf_fuse(r)
    assert [h.episode_id for h in out] == ["a", "b"]


def test_fusion_dedup_and_boost():
    r1 = [Hit("a", 0.9, memory_type="episodic"), Hit("b", 0.8, memory_type="episodic")]
    r2 = [Hit("b", 0.95, memory_type="episodic"), Hit("c", 0.5, memory_type="episodic")]
    out = rrf_fuse(r1, r2)
    ids = [h.episode_id for h in out]
    # b appears in both, should rank higher than a or c
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c"}


def test_type_weight_boosts_priority():
    r = [
        Hit("ep_low", 0.9, memory_type="episodic"),
        Hit("ep_pri", 0.5, memory_type="priority"),
    ]
    out = rrf_fuse(r)
    # priority weight (1.5) outweighs lower rank
    assert out[0].episode_id == "ep_pri"


def test_marks_source_fusion():
    out = rrf_fuse([Hit("a", 1.0)])
    assert out[0].source == "fusion"
```

- [ ] **Step 3: Run**

Run: `python -m pytest tests/unit/test_retriever_fusion.py -v`
Expected: 4 PASSED.

- [ ] **Step 4: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(retriever): RRF fusion with memory-type weights"
```

---

### Task 11: Budget trim + render

**Files:**
- Create: `src/spillover/retriever/budget.py`
- Create: `src/spillover/retriever/render.py`
- Create: `tests/unit/test_retriever_budget.py`
- Create: `tests/unit/test_retriever_render.py`

- [ ] **Step 1: Write `budget.py`**

`src/spillover/retriever/budget.py`:

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
    """Trim hits so that the sum of their content tokens fits under max_tokens.

    Reads each episode's content_json and accumulates until budget is reached.
    """
    if max_tokens <= 0 or not hits:
        return []
    out: list[Hit] = []
    total = 0
    for hit in hits:
        row = db.execute(
            "SELECT content_json FROM episodes WHERE id = ?", (hit.episode_id,)
        ).fetchone()
        if row is None:
            continue
        # token_count column has the cached value too; prefer it when present
        cached = db.execute(
            "SELECT token_count FROM episodes WHERE id = ?", (hit.episode_id,)
        ).fetchone()
        n = int(cached["token_count"]) if cached and cached["token_count"] else count_tokens(row["content_json"])
        if total + n > max_tokens:
            break
        total += n
        out.append(hit)
    return out
```

- [ ] **Step 2: Write `render.py`**

`src/spillover/retriever/render.py`:

```python
from __future__ import annotations

import json
import sqlite3

from spillover.retriever.vector import Hit


def render_ltm_block(db: sqlite3.Connection, hits: list[Hit]) -> str:
    """Render the chosen hits as a single <spillover-ltm> block.

    Each episode is rendered as a fenced section so the model can parse it,
    with its memory_type and a short preamble.
    """
    if not hits:
        return ""
    sections: list[str] = []
    for hit in hits:
        row = db.execute(
            "SELECT role, content_json, memory_type FROM episodes WHERE id = ?",
            (hit.episode_id,),
        ).fetchone()
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
            f"<episode id=\"{hit.episode_id}\" type=\"{row['memory_type']}\" "
            f"role=\"{row['role']}\">\n{text}\n</episode>"
        )
    return (
        "<spillover-ltm>\n"
        "The following are relevant past episodes retrieved from long-term memory.\n"
        "They are NOT part of the active conversation.\n\n"
        + "\n\n".join(sections)
        + "\n</spillover-ltm>"
    )
```

- [ ] **Step 3: Tests**

`tests/unit/test_retriever_budget.py`:

```python
import time

from spillover.archive.writer import Turn, archive_raw
from spillover.retriever.budget import trim_to_budget
from spillover.retriever.vector import Hit
from spillover.storage.sqlite import open_project_db


def test_trim_to_budget_stops_at_cap(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        ids = []
        for i in range(5):
            ids.append(
                archive_raw(
                    db,
                    Turn(
                        project_id="p1",
                        role="user",
                        content="x" * 200,  # ~50 tokens
                        tool_calls=[],
                        code_refs=[],
                        token_count=50,
                        ts=int(time.time() * 1000) + i,
                    ),
                )
            )
        hits = [Hit(eid, 1.0) for eid in ids]
        kept = trim_to_budget(db, hits, max_tokens=120)
        # Each at 50 tokens, budget 120 -> 2 kept
        assert len(kept) == 2
    finally:
        db.close()


def test_trim_zero_budget_returns_empty(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        assert trim_to_budget(db, [Hit("x", 1.0)], max_tokens=0) == []
    finally:
        db.close()
```

`tests/unit/test_retriever_render.py`:

```python
import time

from spillover.archive.writer import Turn, archive_raw
from spillover.retriever.render import render_ltm_block
from spillover.retriever.vector import Hit
from spillover.storage.sqlite import open_project_db


def test_render_empty_returns_empty():
    # No DB needed for empty path
    class _Stub:
        def execute(self, *args, **kwargs):
            raise AssertionError("should not be called")

    assert render_ltm_block(_Stub(), []) == ""


def test_render_wraps_in_block(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="hello world",
                tool_calls=[],
                code_refs=[],
                token_count=2,
                ts=int(time.time() * 1000),
            ),
        )
        db.execute("UPDATE episodes SET memory_type='episodic' WHERE id=?", (eid,))
        out = render_ltm_block(db, [Hit(eid, 1.0)])
        assert out.startswith("<spillover-ltm>")
        assert out.endswith("</spillover-ltm>")
        assert "hello world" in out
        assert f'id="{eid}"' in out
    finally:
        db.close()
```

- [ ] **Step 4: Run**

Run: `python -m pytest tests/unit/test_retriever_budget.py tests/unit/test_retriever_render.py -v`
Expected: 4 PASSED total.

- [ ] **Step 5: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(retriever): token-budget trim + <spillover-ltm> render"
```

---

## Phase 4 — Wire facet worker + retriever into proxy

### Task 12: Config additions + proxy wiring

**Files:**
- Modify: `src/spillover/config.py`
- Modify: `src/spillover/proxy/app.py`
- Create: `tests/integration/test_retriever_lifecycle.py`

This is the integration task. After it lands, the end-to-end flow is:
- Request arrives → middleware sets project_id.
- Proxy parses payload via AnthropicAdapter.
- Retriever runs (embed query → vector + graph → fusion → budget trim → render).
- LTM block prepended to system.
- Adapter rebuilds payload, forwards to Anthropic.
- Response streams back; eviction runs as before (Plan 1).
- Each archived episode also enqueues FacetEvent on `app.state.facet_queue`.

- [ ] **Step 1: Extend `Config`**

`src/spillover/config.py` — replace with:

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
    ltm_budget_pct: float  # fraction of window_max reserved for LTM injection
    retriever_topk: int    # final fused top-K after RRF
    retriever_vector_k: int  # vector top-K before fusion
    retriever_graph_k: int  # graph top-K before fusion

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            port=int(os.environ.get("SPILLOVER_PORT", "8787")),
            watermark=float(os.environ.get("SPILLOVER_WATERMARK", "0.85")),
            window_max=int(os.environ.get("SPILLOVER_WINDOW_MAX", "200000")),
            db_root=Path(
                os.environ.get("SPILLOVER_DB_ROOT", str(Path.home() / ".spillover"))
            ),
            upstream_base_url=os.environ.get(
                "SPILLOVER_UPSTREAM_BASE_URL", "https://api.anthropic.com"
            ),
            ltm_budget_pct=float(os.environ.get("SPILLOVER_LTM_BUDGET_PCT", "0.15")),
            retriever_topk=int(os.environ.get("SPILLOVER_RETRIEVER_TOPK", "8")),
            retriever_vector_k=int(os.environ.get("SPILLOVER_RETRIEVER_VECTOR_K", "50")),
            retriever_graph_k=int(os.environ.get("SPILLOVER_RETRIEVER_GRAPH_K", "50")),
        )
```

Update existing `tests/unit/test_config.py` to add assertions for the new defaults:

```python
# Append to test_config_defaults:
    assert cfg.ltm_budget_pct == 0.15
    assert cfg.retriever_topk == 8
    assert cfg.retriever_vector_k == 50
    assert cfg.retriever_graph_k == 50
```

- [ ] **Step 2: Add retriever helper to `app.py`**

Add at module level in `src/spillover/proxy/app.py`:

```python
from spillover.facet.embed import embed_text
from spillover.facet.entities import extract_entities
from spillover.facet.worker import FacetEvent, FacetWorker
from spillover.retriever.budget import trim_to_budget
from spillover.retriever.fusion import rrf_fuse
from spillover.retriever.graph import graph_walk
from spillover.retriever.render import render_ltm_block
from spillover.retriever.vector import vector_topk
from spillover.storage.kuzu import open_project_kuzu
```

Add helper:

```python
def _retrieve_ltm_block(config: Config, project_id: str, conv) -> str:
    """Run hybrid retrieval and return the <spillover-ltm> string (or empty)."""
    if not conv.turns:
        return ""
    # Query text: last 3 turns concatenated
    recent = conv.turns[-3:]
    query_text = "\n".join(
        t.content if isinstance(t.content, str)
        else " ".join(b.get("text", "") for b in t.content if isinstance(b, dict))
        for t in recent
    )
    if not query_text.strip():
        return ""

    db = open_project_db(config.db_root, project_id)
    try:
        # Check whether vec_episodes has anything for this project
        n = db.execute("SELECT COUNT(*) FROM vec_episodes").fetchone()[0]
        if n == 0:
            return ""
        emb = embed_text(query_text)
        v_hits = vector_topk(db, emb, k=config.retriever_vector_k)

        seeds = [e.name for e in extract_entities(query_text)][:20]
        g_hits: list = []
        if seeds:
            try:
                kuzu_conn = open_project_kuzu(config.db_root, project_id)
                g_hits = graph_walk(
                    kuzu_conn, seeds, k_hop=2, limit=config.retriever_graph_k
                )
            except Exception:
                log = get_logger("retriever")
                log.exception("graph walk failed project=%s", project_id)

        fused = rrf_fuse(v_hits, g_hits)[: config.retriever_topk]
        budget = int(config.window_max * config.ltm_budget_pct)
        trimmed = trim_to_budget(db, fused, max_tokens=budget)
        return render_ltm_block(db, trimmed)
    finally:
        db.close()


def _inject_ltm(payload: dict, ltm_text: str) -> None:
    if not ltm_text:
        return
    existing = payload.get("system")
    if existing is None:
        payload["system"] = ltm_text
    elif isinstance(existing, str):
        payload["system"] = ltm_text + "\n\n" + existing
    elif isinstance(existing, list):
        payload["system"] = [{"type": "text", "text": ltm_text}, *existing]
```

Then in the route handler, BEFORE forwarding the request:

```python
        ltm_text = _retrieve_ltm_block(config, project_id, AnthropicAdapter().parse(payload))
        _inject_ltm(payload, ltm_text)
        body = json.dumps(payload).encode("utf-8")
```

After `_maybe_evict` returns, enqueue facet events for the just-archived episodes. To do this cleanly, change `_maybe_evict` to return the list of episode ids it archived, and have the route push events for each:

(Refactor `_maybe_evict` so its return type is `list[str]`. The current code already accumulates `episode_ids`; just `return episode_ids` at the end.)

Then in the route:

```python
        archived_ids = _maybe_evict(config, project_id, payload, assistant_text, usage)
        if archived_ids and hasattr(app.state, "facet_queue"):
            for eid in archived_ids:
                app.state.facet_queue.put_nowait(
                    FacetEvent(project_id=project_id, episode_id=eid, db_root=config.db_root)
                )
```

Finally, in the `lifespan` context manager, start the facet worker:

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = config
        app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        app.state.facet_queue = asyncio.Queue()
        app.state.facet_worker = FacetWorker(app.state.facet_queue)
        app.state.facet_worker.start()
        try:
            yield
        finally:
            await app.state.facet_worker.stop()
            await app.state.http_client.aclose()
```

(Add `import asyncio` at the top if not already present.)

- [ ] **Step 3: Write integration test**

`tests/integration/test_retriever_lifecycle.py`:

```python
import hashlib
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app
from spillover.storage.sqlite import open_project_db


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def _resp(input_tokens, output_tokens, text="ok"):
    return httpx.Response(
        200,
        json={
            "id": "msg",
            "model": "claude-opus-4-7",
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    )


@pytest.mark.slow
@respx.mock
def test_archived_episode_becomes_retrievable(client, config):
    """End-to-end: a turn evicted in request 1 must be retrieved as LTM in request 2."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_resp(900, 80, text="ok")
    )
    pid = "abcdef12"
    # Request 1: triggers eviction
    messages = []
    for i in range(12):
        role = "user" if i % 2 == 0 else "assistant"
        # Include a distinctive entity so retriever finds it later
        messages.append(
            {"role": role, "content": f"turn {i} about config/foo.py setting watermark"}
        )
    r1 = client.post(
        "/v1/messages",
        headers={"X-Project": pid, "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": messages,
        },
    )
    assert r1.status_code == 200

    # Give facet worker time to process (poll for facet_pending=0)
    db = open_project_db(config.db_root, pid)
    try:
        deadline = time.time() + 30  # generous: model load can take a while
        while time.time() < deadline:
            pending = db.execute(
                "SELECT COUNT(*) FROM episodes WHERE facet_pending=1"
            ).fetchone()[0]
            if pending == 0:
                break
            time.sleep(0.5)
        assert pending == 0, "facet worker did not process episodes in time"
        vec_count = db.execute("SELECT COUNT(*) FROM vec_episodes").fetchone()[0]
        assert vec_count > 0
    finally:
        db.close()

    # Request 2: mention foo.py — retriever must inject LTM
    r2 = client.post(
        "/v1/messages",
        headers={"X-Project": pid, "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "remind me what we did with foo.py"}
            ],
        },
    )
    assert r2.status_code == 200
    # Inspect the most recent intercepted request — its system field should contain LTM
    last_request = route.calls.last.request
    body = last_request.read().decode("utf-8")
    assert "<spillover-ltm>" in body
    assert "foo.py" in body
```

- [ ] **Step 4: Run**

Run: `python -m pytest tests/integration/test_retriever_lifecycle.py -v`
Expected: 1 PASSED (slow — embedder + worker + retrieval).

- [ ] **Step 5: Full suite**

Run: `python -m pytest -v`
Expected: ~70 PASSED.

- [ ] **Step 6: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(proxy): wire facet worker + hybrid retriever + LTM injection"
```

---

## Phase 5 — CLI

### Task 13: `spillover query` + extend `spillover stats`

**Files:**
- Modify: `src/spillover/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Extend `cli.py`**

Add two changes to `src/spillover/cli.py`:

(a) Update the `stats` command to also report `vec_episodes`, `facet_pending` count, and graph node count:

Replace the inner `stats` block with:

```python
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
```

(b) Add a new `query` command:

```python
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
                g = graph_walk(kuzu_conn, seeds, k_hop=2, limit=config.retriever_graph_k)
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
```

- [ ] **Step 2: Extend CLI tests**

Append to `tests/unit/test_cli.py`:

```python


@pytest.mark.slow
def test_query_prints_hits(tmp_path, monkeypatch):
    import struct

    from spillover.archive.writer import Turn, archive_raw

    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="something about foo.py",
                tool_calls=[],
                code_refs=[],
                token_count=5,
                ts=1,
            ),
        )
        # Insert a vec row manually so the retriever has data without running embedder
        vec = [1.0] + [0.0] * 767
        db.execute(
            "INSERT INTO vec_episodes(episode_id, embedding, memory_type, importance, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, struct.pack("<768f", *vec), "episodic", 1.0, 1),
        )
    finally:
        db.close()

    runner = CliRunner()
    result = runner.invoke(main, ["query", "p1", "foo.py"])
    assert result.exit_code == 0
    # query embeds the text live — should at least return our one row
    assert "score=" in result.output


def test_stats_reports_embedded_and_pending(tmp_path, monkeypatch):
    import struct

    from spillover.archive.writer import Turn, archive_raw

    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="x",
                tool_calls=[],
                code_refs=[],
                token_count=1,
                ts=1,
            ),
        )
        vec = [0.0] * 768
        db.execute(
            "INSERT INTO vec_episodes(episode_id, embedding, memory_type, importance, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, struct.pack("<768f", *vec), "episodic", 1.0, 1),
        )
        db.execute("UPDATE episodes SET facet_pending=0 WHERE id=?", (eid,))
    finally:
        db.close()

    runner = CliRunner()
    result = runner.invoke(main, ["stats", "p1"])
    assert "embedded: 1" in result.output
    assert "facet_pending: 0" in result.output
```

- [ ] **Step 3: Run**

Run: `python -m pytest tests/unit/test_cli.py -v -m "not slow"`
Expected: previous 3 + 1 new (stats) pass — 4 PASSED.

Run: `python -m pytest tests/unit/test_cli.py -v`
Expected: 5 PASSED (the query test is slow).

- [ ] **Step 4: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(cli): spillover query + extended stats (embedded, facet_pending)"
```

---

## Phase 6 — Verify + tag

### Task 14: Final suite + tag v0.2.0

- [ ] **Step 1: Run full suite**

```
python -m pytest -v
```

Expected: ~75 PASSED (53 from Plan 1 + ~22 new Plan 2 tests). Mark `-m "not slow"` to skip embedder tests on slow machines.

- [ ] **Step 2: Lint**

```
python -m ruff check src/ tests/
```

Expected: 0 errors.

- [ ] **Step 3: Manual smoke (optional, requires fastembed model)**

```
spillover up
# elsewhere
spillover stats <project>  # should report embedded count
spillover query <project> "foo.py"  # should print ranked hits if any
```

- [ ] **Step 4: Tag**

```
git tag -a v0.2.0 -m "spillover v0.2.0 - retriever (Plan 2)"
```

- [ ] **Step 5: Empty commit marking plan done**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit --allow-empty -m "chore: spillover retriever complete (Plan 2 done)"
```

---

## Definition of Done

1. `pytest -v` exits 0 with ≥ 75 passing tests total.
2. `ruff check src/ tests/` exits 0.
3. The end-to-end retriever lifecycle integration test (`test_archived_episode_becomes_retrievable`) passes — proves: archive → facet pipeline → vector + graph storage → retrieval on next request → LTM injection in outbound payload.
4. `spillover query <project> "<text>"` runs and prints ranked hits.
5. `spillover stats <project>` reports embedded + facet_pending counts.
6. `v0.2.0` tag exists.
7. All commits authored by `luizhcrs <luizhcrs@gmail.com>`, no `Co-Authored-By` trailers.

## What unlocks next

- **Plan 3 (counter-compaction):** usage rewrite, env-var disable, conversation-diff rescue, intercept patterns.
- **Plan 4 (multi-CLI + polish):** OpenAI adapter, decay scheduler, Prometheus metrics endpoint, wrappers for CC/Codex/Cursor/Continue, A/B benchmark vs Plan 1 baseline.

End of plan.
