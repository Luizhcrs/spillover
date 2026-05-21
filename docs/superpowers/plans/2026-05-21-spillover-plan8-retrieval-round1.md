# spillover Plan 8: Retrieval Round 1 (BM25 + Priming + Budget Rebalance)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Take the v1.3.0 baseline of `spillover 0/15 anchors hit` against `vanilla 14/15` and close the gap by attacking the three diagnosed root causes — flat RRF scores, missing system-prompt priming, and top-K vs LTM-budget mismatch. Stop after BM25 + priming + budget rebalance to measure; defer HyDE / Self-RAG / ColBERT to Plan 9 conditional on the new numbers.

**Tech stack:** no new deps — SQLite FTS5 is built-in.

End state: v1.4.0 tagged. Re-running the v1.3.0 baseline harness on v1.4.0 produces a new `docs/eval/baseline-v1.4.0.md` with the delta vs v1.3.0 documented.

---

## File structure

New files:

```
src/spillover/
  storage/fts_schema.sql           # FTS5 virtual table over episode content
  retriever/lexical.py             # bm25_topk()
tests/unit/
  test_storage_fts.py
  test_retriever_lexical.py
docs/eval/
  baseline-v1.4.0.md               # produced by re-running the harness
  baseline-v1.4.0.jsonl
```

Modified files:

```
src/spillover/storage/sqlite.py    # load FTS5 schema on open
src/spillover/archive/writer.py    # also insert into FTS table on archive_raw
src/spillover/facet/worker.py      # update FTS row when content reprocessed (no-op for now)
src/spillover/retriever/fusion.py  # accept N rankings (already does)
src/spillover/retriever/render.py  # add priming sentence to the LTM block
src/spillover/proxy/app.py         # add bm25_hits leg to retrieval; rebalance top-K + per-episode budget
src/spillover/config.py            # SPILLOVER_RETRIEVER_BM25_K env; default top-K reduced from 8 to 5
```

---

## Phase 0 — FTS5 schema + BM25 retriever

### Task 1: FTS5 virtual table

**Files:**
- Create: `src/spillover/storage/fts_schema.sql`
- Modify: `src/spillover/storage/sqlite.py`
- Modify: `src/spillover/archive/writer.py`
- Create: `tests/unit/test_storage_fts.py`

- [ ] **Step 1: Write `fts_schema.sql`**

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    episode_id UNINDEXED,
    body,
    content='',
    tokenize='porter unicode61'
);
```

- [ ] **Step 2: Load the schema in `sqlite.py`**

Add a new path constant and `executescript` call:

```python
_FTS_SCHEMA_PATH = Path(__file__).with_name("fts_schema.sql")
```

After `conn.executescript(_VEC_SCHEMA_PATH.read_text(...))` add:

```python
    conn.executescript(_FTS_SCHEMA_PATH.read_text(encoding="utf-8"))
```

- [ ] **Step 3: Have `archive_raw` populate FTS**

In `archive/writer.py`, after the existing INSERT, add:

```python
        body_text = (
            turn.content
            if isinstance(turn.content, str)
            else json.dumps(turn.content, ensure_ascii=False)
        )
        try:
            db.execute(
                "INSERT INTO episodes_fts(episode_id, body) VALUES (?, ?)",
                (eid, body_text),
            )
        except sqlite3.IntegrityError:
            pass  # already indexed
```

- [ ] **Step 4: Test**

```python
import struct

from spillover.archive.writer import Turn, archive_raw
from spillover.storage.sqlite import open_project_db


def test_fts_index_populated_on_archive(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="auth bug at middleware.py line 42 jwt expiry",
                tool_calls=[],
                code_refs=[],
                token_count=10,
                ts=1,
            ),
        )
        rows = db.execute(
            "SELECT episode_id FROM episodes_fts WHERE body MATCH 'middleware'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["episode_id"] == eid
    finally:
        db.close()


def test_fts_matches_phrase(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="SQLite chosen for local-only deployment",
                tool_calls=[],
                code_refs=[],
                token_count=5,
                ts=1,
            ),
        )
        rows = db.execute(
            "SELECT episode_id FROM episodes_fts WHERE body MATCH 'SQLite OR local'"
        ).fetchall()
        assert any(r["episode_id"] == eid for r in rows)
    finally:
        db.close()
```

- [ ] **Step 5: Run + commit**

```
python -m pytest tests/unit/test_storage_fts.py -v
python -m pytest -v -m "not slow"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(storage): FTS5 episodes_fts virtual table populated on archive_raw"
```

---

### Task 2: `bm25_topk` retriever leg

**Files:**
- Create: `src/spillover/retriever/lexical.py`
- Create: `tests/unit/test_retriever_lexical.py`

- [ ] **Step 1: Write `lexical.py`**

```python
from __future__ import annotations

import re
import sqlite3

from spillover.retriever.vector import Hit


# Lightweight tokenizer for query sanitization. FTS5 MATCH syntax is sensitive
# to special chars and operators; we extract bag-of-words and OR-join.
_TOKEN = re.compile(r"[A-Za-z0-9_]{2,}")


def _query_to_fts(query: str) -> str:
    tokens = _TOKEN.findall(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def bm25_topk(
    db: sqlite3.Connection, query: str, k: int = 50
) -> list[Hit]:
    fts_q = _query_to_fts(query)
    if not fts_q:
        return []
    try:
        rows = db.execute(
            "SELECT f.episode_id, bm25(episodes_fts) AS score, "
            "       e.memory_type, e.ts "
            "FROM episodes_fts f "
            "JOIN episodes e ON e.id = f.episode_id "
            "WHERE f.body MATCH ? "
            "ORDER BY score ASC LIMIT ?",
            (fts_q, k),
        ).fetchall()
    except sqlite3.OperationalError:
        # FTS table absent (older DB) — return empty rather than crash
        return []
    # bm25() returns negative scores; lower is better. We invert so higher = more relevant.
    return [
        Hit(
            episode_id=r["episode_id"],
            score=-float(r["score"]),
            memory_type=r["memory_type"],
            importance=None,
            ts=r["ts"],
            source="bm25",
        )
        for r in rows
    ]
```

- [ ] **Step 2: Test**

```python
from spillover.archive.writer import Turn, archive_raw
from spillover.retriever.lexical import _query_to_fts, bm25_topk
from spillover.storage.sqlite import open_project_db


def test_query_to_fts_strips_noise():
    out = _query_to_fts("where is the auth bug?")
    assert "where" in out
    assert "auth" in out
    assert "bug" in out
    assert "?" not in out
    # `is` and `the` are 2-char and 3-char — the tokenizer keeps them (min length is 2),
    # which is fine because FTS5 strips its own stopwords with porter unicode61.


def test_query_to_fts_empty():
    assert _query_to_fts("???") == ""
    assert _query_to_fts("") == ""


def test_bm25_topk_finds_literal_match(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        for i, content in enumerate([
            "the auth bug is at middleware.py line 42",
            "SQLite chosen over Postgres",
            "Erica wife T1 diabetes Basaglar Fiasp",
        ]):
            archive_raw(
                db,
                Turn(
                    project_id="p1",
                    role="user",
                    content=content,
                    tool_calls=[],
                    code_refs=[],
                    token_count=5,
                    ts=i + 1,
                ),
            )
        hits = bm25_topk(db, "where was the auth bug", k=10)
        assert len(hits) >= 1
        assert hits[0].source == "bm25"
        # The auth bug episode is the only one containing 'auth' or 'bug'
        # Vector cosine would smear; BM25 finds it exactly.
        top_content = db.execute(
            "SELECT body FROM episodes_fts WHERE episode_id = ?",
            (hits[0].episode_id,),
        ).fetchone()
        assert "auth" in top_content["body"].lower() or "bug" in top_content["body"].lower()
    finally:
        db.close()


def test_bm25_topk_handles_no_fts_table(tmp_path):
    """If a project pre-dates Plan 8 FTS, bm25_topk returns [] not raise."""
    import sqlite3
    raw_path = tmp_path / "raw.db"
    conn = sqlite3.connect(raw_path)
    conn.row_factory = sqlite3.Row
    hits = bm25_topk(conn, "anything", k=10)
    assert hits == []
    conn.close()
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_retriever_lexical.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(retriever): bm25_topk lexical leg via SQLite FTS5"
```

---

## Phase 1 — Wire BM25 into the proxy retrieval

### Task 3: Add BM25 leg to `_retrieve_ltm_block`

**Files:**
- Modify: `src/spillover/proxy/app.py`
- Modify: `src/spillover/config.py`

- [ ] **Step 1: Add config**

In `config.py`, add field:

```python
    retriever_bm25_k: int
```

In `from_env`:

```python
            retriever_bm25_k=int(os.environ.get("SPILLOVER_RETRIEVER_BM25_K", "50")),
```

Update `tests/unit/test_config.py` to add `retriever_bm25_k == 50` to defaults assertion.

- [ ] **Step 2: Wire BM25 into retrieval**

In `proxy/app.py`, import:

```python
from spillover.retriever.lexical import bm25_topk
```

Inside `_retrieve_ltm_block`, between the vector_topk and graph_walk calls, add:

```python
        b_hits = bm25_topk(db, query_text, k=config.retriever_bm25_k)
```

Then fuse all three rankings:

```python
        fused = rrf_fuse(v_hits, g_hits, b_hits)[: config.retriever_topk]
```

Increment retriever metric:

```python
        from spillover.metrics.registry import retriever_hits_total
        retriever_hits_total.labels(project=project_id, source="vector").inc(len(v_hits))
        retriever_hits_total.labels(project=project_id, source="graph").inc(len(g_hits))
        retriever_hits_total.labels(project=project_id, source="bm25").inc(len(b_hits))
```

- [ ] **Step 3: Run + commit**

```
python -m pytest -v -m "not slow"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(retriever): wire bm25 leg into hybrid fusion (3-way: vector + graph + bm25)"
```

---

## Phase 2 — System-prompt priming

### Task 4: Add priming sentence in `render_ltm_block`

**Files:**
- Modify: `src/spillover/retriever/render.py`
- Modify: `tests/unit/test_retriever_render.py`

- [ ] **Step 1: Update the rendered preamble**

Replace the preamble inside `render_ltm_block`:

```python
    return (
        "<spillover-ltm>\n"
        "Below are excerpts of YOUR OWN past statements and decisions, retrieved\n"
        "from a long-term memory store keyed on this project. Quote from this\n"
        "block whenever it answers the user's question directly. Treat them as\n"
        "facts you established earlier in this project.\n\n"
        + "\n\n".join(sections)
        + "\n</spillover-ltm>"
    )
```

- [ ] **Step 2: Update the test**

In `test_render_wraps_in_block`, change the assertion:

```python
    assert "Below are excerpts of YOUR OWN past statements" in out
```

- [ ] **Step 3: Commit**

```
python -m pytest tests/unit/test_retriever_render.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(retriever): prime LTM block as 'YOUR OWN past statements' so model treats as ground truth"
```

---

## Phase 3 — Top-K / budget rebalance

### Task 5: Default top-K from 8 to 5

**Files:**
- Modify: `src/spillover/config.py`

- [ ] **Step 1: Change default**

```python
            retriever_topk=int(os.environ.get("SPILLOVER_RETRIEVER_TOPK", "5")),
```

- [ ] **Step 2: Update config tests**

In `tests/unit/test_config.py`, change the default assertion from `8` to `5`:

```python
    assert cfg.retriever_topk == 5
```

- [ ] **Step 3: Commit**

```
python -m pytest tests/unit/test_config.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(config): default retriever_topk 8 -> 5 (give each hit more budget headroom)"
```

---

## Phase 4 — Re-run baseline + publish v1.4.0

### Task 6: Re-run the v1.3.0 bench with v1.4.0 code

This task is operational, not code. Document the procedure so it's reproducible.

- [ ] **Step 1: Start proxy with the same low-ceiling config**

```bash
powershell.exe "Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
sleep 2
cd C:/Users/luiz.rs/Documents/Projects/spillover
SPILLOVER_OPERATIONAL_CEILING_TOKENS=400 SPILLOVER_WATERMARK=0.5 nohup spillover up > /tmp/spillover_bench_v140.log 2>&1 &
sleep 5
curl -s http://127.0.0.1:8787/health
```

- [ ] **Step 2: Run the bench against the same dataset**

```bash
spillover bench --run \
  --tasks src/spillover/bench/tasks_baseline.jsonl \
  --report docs/eval/baseline-v1.4.0.md \
  --proxy-url http://127.0.0.1:8787 \
  --model claude-haiku-4-5-20251001
```

- [ ] **Step 3: Inspect + commit**

```
cat docs/eval/baseline-v1.4.0.md
git add docs/eval/baseline-v1.4.0.md docs/eval/baseline-v1.4.0.jsonl
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "eval: publish baseline-v1.4.0 (BM25 + priming + topk rebalance)"
```

- [ ] **Step 4: Write a delta analysis**

Add `docs/eval/analysis-v1.4.0.md` with the same shape as `analysis-v1.3.0.md`. Document:
- Headline numbers (vanilla vs spillover)
- Delta vs v1.3.0
- Which anchors flipped from miss → hit, which still miss
- What Plan 9 should target next

- [ ] **Step 5: Commit the analysis**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "eval: analysis-v1.4.0 — delta vs v1.3.0 baseline"
```

- [ ] **Step 6: Tag + push**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit --allow-empty -m "chore: spillover v1.4.0 (Plan 8 done - retrieval round 1)"
git tag -a v1.4.0 -m "spillover v1.4.0 - BM25 + LTM priming + topk rebalance"
git push -u origin feat/plan8-retrieval-round1
git push origin --tags
git checkout master
git merge --no-ff feat/plan8-retrieval-round1 -m "Merge Plan 8: retrieval round 1 (v1.4.0)"
git push origin master
git push origin --tags
```

---

## Definition of Done

1. Plan 1–7 tests still pass; new tests add ~8.
2. `ruff check src/ tests/` exits 0.
3. FTS5 episodes_fts populated by every `archive_raw`.
4. `bm25_topk` returns hits with `source="bm25"` from SQLite FTS5.
5. Hybrid retrieval fuses **three** rankings: vector + graph + BM25.
6. LTM block preamble primes the model with "YOUR OWN past statements".
7. Default `retriever_topk` is 5 (was 8).
8. `docs/eval/baseline-v1.4.0.md` exists with measured numbers.
9. `docs/eval/analysis-v1.4.0.md` documents the delta and remaining gaps.
10. `v1.4.0` tag exists locally + pushed.

End of plan.
