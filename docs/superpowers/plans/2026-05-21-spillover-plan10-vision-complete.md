# spillover Plan 10: Vision Complete + Logic Retention Tests

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining gap between the user's original vision (dumped in the 2nd conversation message) and spillover v1.5.1. Five deliverables:

1. `placement=between` — fourth LTM placement matching the literal `[SYS][ACTIVE][LTM][USER]` layout.
2. Causality graph queries — make Kuzu's `AFTER` edges queryable as causal chains via a new retriever leg.
3. Open-tasks classifier — detect `TODO`, `FIXME`, "pending", "still need to" patterns; new memory_type `task` joins the existing 4 types.
4. Long-conversation bench harness — programmatic 100-turn synthetic conversations that force eviction in mid-flight, with anchor questions in the evicted region.
5. Logic-retention test fixture — a multi-turn build-a-landing-page scenario with named details (hex colors, copy strings, CTA text, font choices). End-of-conversation questions verify each detail.

End state: v1.6.0 tagged. Real long-conversation bench numbers published. Logic test produces a `pass/fail` per detail, not just anchor strings.

---

## File structure

New files:

```
src/spillover/
  proxy/app.py                       # MODIFIED: placement=between branch
  retriever/causal.py                # NEW: causality_chain() leg
  facet/tasks.py                     # NEW: extract_open_tasks() + task memory_type
  facet/classifier.py                # MODIFIED: detect "task" type
  bench/long_conversation.py         # NEW: programmatic 100-turn gen
  bench/landing_page_scenario.py     # NEW: scripted landing page build conversation
tests/unit/
  test_placement_between.py
  test_causality.py
  test_open_tasks.py
  test_long_conversation_bench.py
  test_landing_page_logic.py
docs/eval/
  long-conversation-v1.6.0.md        # produced by re-bench
  landing-page-logic-v1.6.0.md       # produced by logic harness
```

Modified files:

```
src/spillover/proxy/app.py           # placement=between branch + wire causal leg
src/spillover/retriever/fusion.py    # accept variable number of legs (already does)
src/spillover/facet/worker.py        # write AFTER edge between consecutive episodes
src/spillover/facet/classifier.py    # 5-way classifier (+ task)
src/spillover/storage/schema.sql     # CHECK constraint update for memory_type
src/spillover/storage/kuzu_schema.cypher # AFTER edge already declared, ensure indexed
src/spillover/cli.py                 # `spillover bench-long` + `spillover bench-logic`
README.md                            # document placement=between + logic tests
```

---

## Phase 0 — `placement=between`

### Task 1: Implement `between` placement

**Files:**
- Modify: `src/spillover/proxy/app.py`
- Create: `tests/unit/test_placement_between.py`

The literal layout the user originally specified:

```
[SYSTEM original]
[active turns 0..N-1]
[<spillover-ltm> block as one synthetic user→assistant pair]
[active turn N (the new user message)]
```

The synthetic pair lives BETWEEN the last existing turn and the new user message. Different from `turns` (which inserts at the START of messages) — `between` inserts at the END of messages but BEFORE the final user.

- [ ] **Step 1: Extend `_inject_ltm`**

In `src/spillover/proxy/app.py`, in the existing `_inject_ltm`, add a fourth branch:

```python
    if placement == "between":
        messages = payload.get("messages") or []
        if not messages:
            payload["system"] = ltm_text
            return
        # Find the LAST user message and insert the synthetic pair BEFORE it
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                synthetic = [
                    {
                        "role": "user",
                        "content": (
                            "Before answering, recall the following retrieved from "
                            "long-term memory of this project."
                        ),
                    },
                    {"role": "assistant", "content": ltm_text},
                ]
                payload["messages"] = (
                    list(messages[:i]) + synthetic + list(messages[i:])
                )
                return
        payload["system"] = ltm_text
```

- [ ] **Step 2: Test**

```python
import pytest
from unittest.mock import patch

from spillover.proxy.app import _inject_ltm


def _payload(*turns):
    return {"model": "x", "max_tokens": 100, "messages": list(turns)}


@patch.dict("os.environ", {"SPILLOVER_LTM_PLACEMENT": "between"})
def test_between_inserts_before_last_user():
    payload = _payload(
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2 NEW"},
    )
    _inject_ltm(payload, "<ltm>X</ltm>")
    msgs = payload["messages"]
    # Synthetic pair inserted between a1 and u2
    assert msgs[0]["content"] == "u1"
    assert msgs[1]["content"] == "a1"
    assert msgs[2]["role"] == "user"
    assert "recall" in msgs[2]["content"]
    assert msgs[3]["role"] == "assistant"
    assert msgs[3]["content"] == "<ltm>X</ltm>"
    assert msgs[4]["content"] == "u2 NEW"


@patch.dict("os.environ", {"SPILLOVER_LTM_PLACEMENT": "between"})
def test_between_fallback_when_no_user():
    payload = _payload({"role": "assistant", "content": "a1"})
    _inject_ltm(payload, "<ltm>X</ltm>")
    assert payload.get("system") == "<ltm>X</ltm>"


@patch.dict("os.environ", {"SPILLOVER_LTM_PLACEMENT": "between"})
def test_between_single_user_turn():
    """Same shape as the bench: only one user turn, no history. Inserts before it."""
    payload = _payload({"role": "user", "content": "question"})
    _inject_ltm(payload, "<ltm>X</ltm>")
    msgs = payload["messages"]
    assert len(msgs) == 3
    assert msgs[0]["role"] == "user"
    assert "recall" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "<ltm>X</ltm>"
    assert msgs[2]["content"] == "question"
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_placement_between.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(proxy): LTM placement=between — synthetic pair before last user turn"
```

---

## Phase 1 — Causality graph queries

### Task 2: `AFTER` edges populated by facet worker + `causality_chain` retriever

**Files:**
- Modify: `src/spillover/facet/worker.py`
- Create: `src/spillover/retriever/causal.py`
- Create: `tests/unit/test_causality.py`
- Modify: `src/spillover/proxy/app.py` (wire as 4th retriever leg)

- [ ] **Step 1: Worker writes `AFTER` edges**

In `src/spillover/facet/worker.py`, inside `_process_one` after creating the Episode node, add:

```python
    # Link this episode to the previous one in temporal order (if any)
    prev = kuzu_conn.execute(
        "MATCH (e:Episode) WHERE e.ts < $ts "
        "RETURN e.id, e.ts ORDER BY e.ts DESC LIMIT 1",
        {"ts": ts},
    )
    if prev.has_next():
        prev_id, _ = prev.get_next()
        kuzu_conn.execute(
            "MATCH (a:Episode {id: $a}), (b:Episode {id: $b}) "
            "MERGE (a)-[:AFTER]->(b)",
            {"a": prev_id, "b": event.episode_id},
        )
```

- [ ] **Step 2: `retriever/causal.py`**

```python
from __future__ import annotations

import kuzu

from spillover.retriever.vector import Hit


def causality_chain(
    conn: kuzu.Connection,
    seed_episode_ids: list[str],
    depth: int = 3,
    limit: int = 30,
) -> list[Hit]:
    """For each seed episode, walk AFTER edges up to `depth` hops and return
    Episodes in the chain.

    Score = 1.0 for direct AFTER, 0.7 at hop 2, 0.5 at hop 3.
    """
    if not seed_episode_ids:
        return []
    scores: dict[str, float] = {}
    for hop in range(1, depth + 1):
        decay = 1.0 if hop == 1 else (0.7 if hop == 2 else 0.5)
        path_pattern = "-[:AFTER]->" * hop
        # Both directions: episodes BEFORE and AFTER the seed
        for direction in ("forward", "backward"):
            if direction == "forward":
                q = (
                    f"MATCH (s:Episode)-[:AFTER]->{path_pattern[6:]}(e:Episode) "
                    if hop > 1
                    else "MATCH (s:Episode)-[:AFTER]->(e:Episode) "
                ) + (
                    "WHERE s.id IN $ids "
                    "RETURN DISTINCT e.id, e.memory_type, e.importance, e.ts "
                    "LIMIT $limit"
                )
            else:
                q = (
                    f"MATCH (e:Episode){path_pattern}(s:Episode) "
                    "WHERE s.id IN $ids "
                    "RETURN DISTINCT e.id, e.memory_type, e.importance, e.ts "
                    "LIMIT $limit"
                )
            try:
                res = conn.execute(q, {"ids": seed_episode_ids, "limit": limit})
            except Exception:
                continue
            while res.has_next():
                eid, mt, imp, ts = res.get_next()
                scores[eid] = max(scores.get(eid, 0.0), decay)

    hits = [
        Hit(
            episode_id=eid,
            score=score,
            memory_type=None,  # filled later if needed
            importance=None,
            ts=None,
            source="causal",
        )
        for eid, score in scores.items()
    ]
    hits.sort(key=lambda h: -h.score)
    return hits[:limit]
```

- [ ] **Step 3: Tests**

```python
from spillover.retriever.causal import causality_chain
from spillover.storage.kuzu import open_project_kuzu


def test_causality_chain_forward_one_hop(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    conn.execute("CREATE (a:Episode {id: 'a', ts: 1, memory_type: 'episodic', importance: 1.0})")
    conn.execute("CREATE (b:Episode {id: 'b', ts: 2, memory_type: 'episodic', importance: 1.0})")
    conn.execute("CREATE (c:Episode {id: 'c', ts: 3, memory_type: 'episodic', importance: 1.0})")
    conn.execute("MATCH (a:Episode {id: 'a'}), (b:Episode {id: 'b'}) CREATE (a)-[:AFTER]->(b)")
    conn.execute("MATCH (b:Episode {id: 'b'}), (c:Episode {id: 'c'}) CREATE (b)-[:AFTER]->(c)")

    hits = causality_chain(conn, ["a"], depth=1)
    ids = [h.episode_id for h in hits]
    assert "b" in ids
    assert "c" not in ids  # 2 hops away


def test_causality_chain_multi_hop(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    conn.execute("CREATE (a:Episode {id: 'a', ts: 1, memory_type: 'episodic', importance: 1.0})")
    conn.execute("CREATE (b:Episode {id: 'b', ts: 2, memory_type: 'episodic', importance: 1.0})")
    conn.execute("CREATE (c:Episode {id: 'c', ts: 3, memory_type: 'episodic', importance: 1.0})")
    conn.execute("MATCH (a:Episode {id: 'a'}), (b:Episode {id: 'b'}) CREATE (a)-[:AFTER]->(b)")
    conn.execute("MATCH (b:Episode {id: 'b'}), (c:Episode {id: 'c'}) CREATE (b)-[:AFTER]->(c)")

    hits = causality_chain(conn, ["a"], depth=3)
    ids = [h.episode_id for h in hits]
    assert "b" in ids
    assert "c" in ids


def test_causality_chain_empty_seeds(tmp_path):
    conn = open_project_kuzu(tmp_path, "p1")
    assert causality_chain(conn, [], depth=2) == []
```

- [ ] **Step 4: Wire into proxy `_retrieve_ltm_block`**

In `app.py` after `b_hits = bm25_topk(...)`, add:

```python
        # Causal leg: use BM25/vector top hit episode ids as seeds
        seed_ids = [h.episode_id for h in (v_hits[:3] + b_hits[:3])]
        c_hits: list = []
        if seed_ids:
            try:
                kuzu_conn = open_project_kuzu(config.db_root, project_id)
                from spillover.retriever.causal import causality_chain
                c_hits = causality_chain(kuzu_conn, seed_ids, depth=2)
            except Exception:
                _log.exception("causal walk failed project=%s", project_id)

        fused = rrf_fuse(v_hits, g_hits, b_hits, c_hits)[: config.retriever_topk]
        from spillover.metrics.registry import retriever_hits_total
        retriever_hits_total.labels(project=project_id, source="vector").inc(len(v_hits))
        retriever_hits_total.labels(project=project_id, source="graph").inc(len(g_hits))
        retriever_hits_total.labels(project=project_id, source="bm25").inc(len(b_hits))
        retriever_hits_total.labels(project=project_id, source="causal").inc(len(c_hits))
```

- [ ] **Step 5: Run + commit**

```
python -m pytest tests/unit/test_causality.py -v
python -m pytest -v -m "not slow"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(causal): AFTER-edge populated + causality_chain retriever leg (4-way fusion)"
```

---

## Phase 2 — Open-tasks classifier + `task` memory_type

### Task 3: Detect TODO / FIXME / pending state as `task` type

**Files:**
- Create: `src/spillover/facet/tasks.py`
- Modify: `src/spillover/facet/classifier.py`
- Modify: `src/spillover/storage/schema.sql` (relax CHECK on memory_type if any)
- Create: `tests/unit/test_open_tasks.py`

- [ ] **Step 1: `facet/tasks.py`**

```python
from __future__ import annotations

import re
from typing import Any


_OPEN_TASK_PATTERNS = [
    re.compile(r"(?i)\bTODO\b"),
    re.compile(r"(?i)\bFIXME\b"),
    re.compile(r"(?i)\bXXX\b"),
    re.compile(r"(?i)\bHACK\b"),
    re.compile(r"(?i)\bpending\b"),
    re.compile(r"(?i)\bnot yet (done|implemented|finished)\b"),
    re.compile(r"(?i)\bstill (need to|have to|must)\b"),
    re.compile(r"(?i)\bnext step[s]? (is|are)\b"),
    re.compile(r"(?i)\bfaltam? (fazer|implementar)\b"),  # PT-BR
    re.compile(r"(?i)\bainda (preciso|falta|tem que)\b"),
    re.compile(r"(?i)\bproxim[oa] passo\b"),
]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def has_open_task(content: Any) -> bool:
    text = _content_to_text(content)
    return any(p.search(text) for p in _OPEN_TASK_PATTERNS)
```

- [ ] **Step 2: Update `classifier.py` to a 5-way classifier**

```python
from typing import Any, Literal

from spillover.facet.tasks import has_open_task

MemoryType = Literal["procedural", "episodic", "semantic", "priority", "task"]


def classify(content: Any, tool_calls: list[dict] | None = None) -> MemoryType:
    text = _content_to_text(content)
    has_tools = bool(tool_calls)

    if has_open_task(content):
        return "task"
    if _PRIORITY_MARKERS.search(text):
        return "priority"
    if has_tools or _PROCEDURAL_MARKERS.search(text):
        return "procedural"
    if _SEMANTIC_MARKERS.search(text):
        return "semantic"
    return "episodic"
```

(Open tasks beat priority because "still need to" implies open-state that a `priority` flag would mis-tag.)

- [ ] **Step 3: Update RRF type weights to include `task`**

In `src/spillover/retriever/fusion.py`:

```python
DEFAULT_TYPE_WEIGHTS = {
    "task": 1.4,       # NEW — open tasks are highly retrieval-worthy
    "priority": 1.5,
    "procedural": 1.2,
    "episodic": 1.0,
    "semantic": 1.0,
}
```

- [ ] **Step 4: Update decay half-life for task type**

In `src/spillover/decay/scheduler.py`:

```python
HALF_LIFE_HOURS = {
    "priority": 60 * 24,
    "procedural": 30 * 24,
    "semantic": 14 * 24,
    "episodic": 7 * 24,
    "task": 90 * 24,   # NEW — open tasks decay slowest (still-open is long-lived signal)
}
```

And the base importance map in `_apply_decay_for_project`:

```python
base = {
    "task": 0.95,
    "priority": 1.0,
    "procedural": 0.7,
    "semantic": 0.6,
    "episodic": 0.5,
}.get(r["memory_type"], 0.5)
```

Same in `facet/worker._base_importance`:

```python
def _base_importance(memory_type: str, tool_call_count: int) -> float:
    base = {
        "task": 0.95,
        "priority": 1.0,
        "procedural": 0.7,
        "semantic": 0.6,
        "episodic": 0.5,
    }[memory_type]
    return min(1.0, base + 0.05 * tool_call_count)
```

- [ ] **Step 5: Tests**

```python
from spillover.facet.classifier import classify
from spillover.facet.tasks import has_open_task


def test_detects_todo_marker():
    assert has_open_task("TODO: implement the BM25 leg")


def test_detects_fixme_marker():
    assert has_open_task("FIXME this regex is fragile")


def test_detects_pending_english():
    assert has_open_task("we still need to write the chaos test")


def test_detects_ptbr_pending():
    assert has_open_task("ainda preciso integrar o decay scheduler")
    assert has_open_task("faltam fazer os testes de integração")


def test_no_open_task_in_done_work():
    assert not has_open_task("implemented the BM25 leg and committed.")


def test_classifier_picks_task_over_priority():
    """Open-task wins over priority — we want pending items to surface even
    when described in important-sounding language."""
    result = classify("TODO: this is important — fix the auth bug")
    assert result == "task"


def test_classifier_falls_through_to_episodic_without_task():
    result = classify("ran the tests they passed")
    assert result == "episodic"
```

- [ ] **Step 6: Run + commit**

```
python -m pytest tests/unit/test_open_tasks.py -v
python -m pytest -v -m "not slow"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(facet): task memory_type — TODO/FIXME/pending classifier with PT-BR + EN patterns"
```

---

## Phase 3 — Long-conversation bench harness

### Task 4: `bench/long_conversation.py` — programmatic 100-turn scenarios

**Files:**
- Create: `src/spillover/bench/long_conversation.py`
- Create: `tests/unit/test_long_conversation_bench.py`

The harness generates a synthetic conversation:

1. 80 turns of mixed work — file reads, decisions, tool runs, casual exchanges. Each turn ~80-120 real tokens.
2. Programmatically embed **anchor facts** at known positions (turns 5, 15, 30, 50). Examples: "we picked SQLite over Postgres because X", "the auth bug is on line 42", "ADR-014 explains the legacy drop".
3. 20 more turns of unrelated work to push the anchored turns OUT of the active window (with `operational_ceiling_tokens` set just below the conversation's natural total).
4. Final question: "what was the database choice and why?", referencing a fact embedded at turn 5 (now evicted).
5. Measure if the model's response includes the anchor strings.

The control: same conversation sent vanilla (truncated to ceiling). The test: same conversation routed through spillover (full history available via LTM).

- [ ] **Step 1: Write `bench/long_conversation.py`**

```python
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx


@dataclass
class LongConvResult:
    scenario_id: str
    mode: str  # "vanilla_truncated" | "spillover"
    response: str
    input_tokens: int
    output_tokens: int
    anchors_hit: list[str]
    anchors_missed: list[str]
    latency_ms: int
    error: str | None = None


@dataclass
class LongConvScenario:
    id: str
    pre_turns: list[dict]   # background work, no anchors
    anchor_turns: list[dict]  # turns containing the facts we want to recover
    filler_turns: list[dict]  # post-anchor work that pushes anchors out
    question: str
    expected_anchors: list[str]


def make_scenario_db_choice() -> LongConvScenario:
    """Scenario: 80 turns of work, db choice fact at turn 5, asked at end."""
    pre = []
    anchor = [
        {"role": "user", "content": "We're at the storage layer decision point. Local-only deployment, max few hundred GB, single user. Postgres or SQLite?"},
        {"role": "assistant", "content": "Given local-only + few hundred GB ceiling + zero-infra requirement, SQLite is the right call. Postgres adds a daemon + auth + connection pooling we don't need. Decision: SQLite. Reason: local-only + zero-infra + scale-fits."},
    ]
    filler = []
    # Fill to 80 turns total
    for i in range(38):
        pre.append({"role": "user", "content": f"sub-task {i:02d}: list files in src/, check imports, look for circular dependencies"})
        pre.append({"role": "assistant", "content": f"checked src/ at iteration {i:02d}, no circular imports found, structure clean"})
    for i in range(20):
        filler.append({"role": "user", "content": f"unrelated work {i:02d}: refactor the http client to use httpx instead of requests"})
        filler.append({"role": "assistant", "content": f"refactor pass {i:02d} done, httpx 0.27 wired, sync calls migrated"})
    return LongConvScenario(
        id="db_choice_long",
        pre_turns=pre,
        anchor_turns=anchor,
        filler_turns=filler,
        question="Remind me which database we picked and why exactly.",
        expected_anchors=["SQLite", "local"],
    )


def make_scenario_auth_bug() -> LongConvScenario:
    pre = []
    anchor = [
        {"role": "user", "content": "Bug in auth: tokens that expire AT THE EXACT MOMENT of the request are accepted instead of rejected. The off-by-one is in middleware.py line 42 — the comparison uses `<` instead of `<=`."},
        {"role": "assistant", "content": "Confirmed: auth bug at middleware.py:42. Operator is `<` when it should be `<=`. Classic off-by-one on the jwt expiry boundary. Tests reproduce."},
    ]
    filler = []
    for i in range(38):
        pre.append({"role": "user", "content": f"investigate logging config item {i:02d}, verify formatter, check log levels"})
        pre.append({"role": "assistant", "content": f"logging config item {i:02d}: structured json, level INFO, formatter consistent"})
    for i in range(20):
        filler.append({"role": "user", "content": f"docs cleanup task {i:02d}: rewrite the section on configuration env vars"})
        filler.append({"role": "assistant", "content": f"docs section {i:02d} rewritten, table format applied"})
    return LongConvScenario(
        id="auth_bug_long",
        pre_turns=pre,
        anchor_turns=anchor,
        filler_turns=filler,
        question="Where exactly was the auth bug and what kind of bug was it?",
        expected_anchors=["middleware", "42", "jwt"],
    )


def all_scenarios() -> list[LongConvScenario]:
    return [make_scenario_db_choice(), make_scenario_auth_bug()]


def _check_anchors(text: str, anchors: list[str]) -> tuple[list[str], list[str]]:
    hits = [a for a in anchors if a.lower() in text.lower()]
    misses = [a for a in anchors if a not in hits]
    return hits, misses


def _extract_text(resp: dict) -> str:
    return "".join(
        b.get("text", "")
        for b in resp.get("content", [])
        if isinstance(b, dict)
    )


def _call(base_url: str, auth: str, payload: dict, extra_headers: dict | None = None) -> tuple[int, dict]:
    headers = {
        "Authorization": auth,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    with httpx.Client(timeout=120.0) as client:
        r = client.post(f"{base_url}/v1/messages", json=payload, headers=headers)
        r.raise_for_status()
        return r.status_code, r.json()


def run_vanilla_truncated(
    scenario: LongConvScenario,
    base_url: str,
    auth: str,
    model: str,
    keep_last_n: int = 8,
) -> LongConvResult:
    """Send conversation with only the last N turns + question. Anchor likely lost."""
    all_turns = scenario.pre_turns + scenario.anchor_turns + scenario.filler_turns
    kept = all_turns[-keep_last_n:] + [{"role": "user", "content": scenario.question}]
    t0 = time.time()
    try:
        _, resp = _call(base_url, auth, {"model": model, "max_tokens": 300, "messages": kept})
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check_anchors(text, scenario.expected_anchors)
        return LongConvResult(
            scenario_id=scenario.id, mode="vanilla_truncated", response=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            anchors_hit=hits, anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return LongConvResult(
            scenario_id=scenario.id, mode="vanilla_truncated", response="",
            input_tokens=0, output_tokens=0,
            anchors_hit=[], anchors_missed=scenario.expected_anchors,
            latency_ms=int((time.time() - t0) * 1000), error=str(e),
        )


def run_spillover(
    scenario: LongConvScenario,
    proxy_base_url: str,
    auth: str,
    model: str,
) -> LongConvResult:
    """Send full conversation through spillover; eviction archives mid-flight."""
    all_turns = scenario.pre_turns + scenario.anchor_turns + scenario.filler_turns
    full = all_turns + [{"role": "user", "content": scenario.question}]
    t0 = time.time()
    try:
        _, resp = _call(
            proxy_base_url, auth,
            {"model": model, "max_tokens": 300, "messages": full},
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check_anchors(text, scenario.expected_anchors)
        return LongConvResult(
            scenario_id=scenario.id, mode="spillover", response=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            anchors_hit=hits, anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return LongConvResult(
            scenario_id=scenario.id, mode="spillover", response="",
            input_tokens=0, output_tokens=0,
            anchors_hit=[], anchors_missed=scenario.expected_anchors,
            latency_ms=int((time.time() - t0) * 1000), error=str(e),
        )


def render_report(results: list[LongConvResult]) -> str:
    by_mode = {"vanilla_truncated": [], "spillover": []}
    for r in results:
        by_mode[r.mode].append(r)

    def _full_hit(rs):
        return sum(1 for r in rs if not r.anchors_missed) if rs else 0

    lines = ["# Long-conversation bench", "", "## summary", "", "| metric | vanilla_truncated | spillover |", "|---|---:|---:|"]
    v = by_mode["vanilla_truncated"]
    s = by_mode["spillover"]
    lines.append(f"| scenarios w/ all anchors hit | {_full_hit(v)}/{len(v)} | {_full_hit(s)}/{len(s)} |")
    lines.append(f"| total input_tokens | {sum(r.input_tokens for r in v)} | {sum(r.input_tokens for r in s)} |")
    lines.append(f"| total output_tokens | {sum(r.output_tokens for r in v)} | {sum(r.output_tokens for r in s)} |")
    lines.append(f"| total errors | {sum(1 for r in v if r.error)} | {sum(1 for r in s if r.error)} |")

    lines.append("\n## per-scenario\n")
    lines.append("| scenario | mode | hits | misses | input | output | latency_ms |")
    lines.append("|---|---|---|---|---:|---:|---:|")
    for r in results:
        hits = ",".join(r.anchors_hit) or "-"
        misses = ",".join(r.anchors_missed) or "-"
        lines.append(
            f"| {r.scenario_id} | {r.mode} | {hits} | {misses} | {r.input_tokens} | {r.output_tokens} | {r.latency_ms} |"
        )
    return "\n".join(lines) + "\n"
```

- [ ] **Step 2: CLI subcommand**

In `src/spillover/cli.py`:

```python
@main.command(name="bench-long")
@click.option("--proxy-url", default="http://127.0.0.1:8787")
@click.option("--vanilla-url", default="https://api.anthropic.com")
@click.option("--report", default="bench-long-report.md")
@click.option("--model", default="claude-haiku-4-5-20251001")
def bench_long(proxy_url: str, vanilla_url: str, report: str, model: str):
    """Run the long-conversation bench (anchor facts embedded mid-history)."""
    import hashlib
    import json
    import os
    import uuid
    from pathlib import Path

    from spillover.bench.long_conversation import (
        all_scenarios, render_report, run_spillover, run_vanilla_truncated,
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
```

- [ ] **Step 3: Test (unit only — don't hit network)**

```python
from spillover.bench.long_conversation import (
    LongConvResult,
    _check_anchors,
    _extract_text,
    all_scenarios,
    render_report,
)


def test_all_scenarios_have_anchors_and_question():
    sc = all_scenarios()
    assert len(sc) >= 2
    for s in sc:
        assert s.question
        assert s.expected_anchors
        assert len(s.pre_turns) >= 40  # at least 40 turns of background
        assert len(s.anchor_turns) >= 1
        assert len(s.filler_turns) >= 20  # at least 20 turns of post-anchor filler


def test_check_anchors_case_insensitive():
    hits, misses = _check_anchors("we picked SQLite for the local case", ["sqlite", "local", "postgres"])
    assert hits == ["sqlite", "local"]
    assert misses == ["postgres"]


def test_render_report_per_scenario_rows():
    results = [
        LongConvResult("sc1", "vanilla_truncated", "x", 100, 50, ["foo"], []),
        LongConvResult("sc1", "spillover", "x", 200, 50, ["foo"], []),
    ]
    md = render_report(results)
    assert "| sc1 | vanilla_truncated |" in md
    assert "| sc1 | spillover |" in md
```

- [ ] **Step 4: Run + commit**

```
python -m pytest tests/unit/test_long_conversation_bench.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(bench): long-conversation harness with anchor-in-history scenarios + spillover bench-long"
```

---

## Phase 4 — Landing-page logic-retention test

### Task 5: `bench/landing_page_scenario.py`

**Files:**
- Create: `src/spillover/bench/landing_page_scenario.py`
- Create: `tests/unit/test_landing_page_logic.py`
- Modify: `src/spillover/cli.py` (add `bench-logic` subcommand)

The landing-page scenario is a single very long conversation in which the user and assistant collaboratively build a fictitious landing page for spillover itself. Across ~60 turns the conversation establishes:

- Primary CTA: `"Stop compacting. Start spilling over."`
- Accent color hex: `#06FFB0`
- Body font: `Inter`
- Heading font: `Geist Mono`
- Hero headline: `"Agents never forget — they spill over"`
- Section count: 5
- Pricing CTA text: `"Get the proxy"`
- Email field placeholder: `"work@yourcompany.com"`
- Footer year: `2026`

Then the conversation drifts for 20+ turns into unrelated work (writing tests, debugging an unrelated thing) so the early decisions slide out of the active window. Then the question — one for each decision — gets asked.

Pass/fail per decision, not aggregate. This is dogfood-shaped: it tests whether spillover actually preserves specific named details across a long working session.

- [ ] **Step 1: Write `bench/landing_page_scenario.py`**

```python
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx


@dataclass
class DetailCheck:
    name: str
    question: str
    expected: list[str]    # any one of these tokens must appear


@dataclass
class LogicResult:
    detail: str
    mode: str
    response: str
    hits: list[str]
    missed_all: bool
    input_tokens: int
    output_tokens: int
    latency_ms: int
    error: str | None = None


LANDING_PAGE_DETAILS: list[DetailCheck] = [
    DetailCheck("primary_cta", "What's the primary CTA text we agreed on?", ["Stop compacting", "Start spilling over"]),
    DetailCheck("accent_hex", "What hex color did we pick for accent?", ["#06FFB0", "06FFB0", "06ffb0"]),
    DetailCheck("body_font", "What font did we pick for body copy?", ["Inter"]),
    DetailCheck("heading_font", "What font is the heading?", ["Geist Mono", "Geist"]),
    DetailCheck("hero_headline", "What was the hero headline?", ["Agents never forget", "spill over"]),
    DetailCheck("section_count", "How many sections total?", ["five", "5"]),
    DetailCheck("pricing_cta", "What does the pricing-section CTA say?", ["Get the proxy"]),
    DetailCheck("email_placeholder", "What's the placeholder text in the email field?", ["work@yourcompany.com"]),
    DetailCheck("footer_year", "What year is in the footer?", ["2026"]),
]


def build_landing_page_history() -> list[dict]:
    """Build a ~50-turn conversation that establishes every detail above."""
    turns: list[dict] = []

    # Establish each decision across the first ~30 turns. Mix in unrelated chatter.
    decisions = [
        ("Let's draft the primary CTA. I'm leaning toward 'Stop compacting. Start spilling over.' — direct, punchy, names the problem.",
         "Agreed. Primary CTA: 'Stop compacting. Start spilling over.' It anchors the architectural opposition. Final."),
        ("Accent color — I want it to feel technical but alive. Mint-cyan in the #06FFB0 range.",
         "Locked in: accent #06FFB0. High-contrast on dark, accessible on light. Use for CTAs and active state highlights."),
        ("Body font: clean sans, modern. Inter has the right weight scale.",
         "Body font: Inter. Variable weight, good at small sizes. Decision recorded."),
        ("Heading font should contrast — mono feels right for a developer audience. Geist Mono.",
         "Heading font: Geist Mono. Pairs cleanly with Inter body. Decision final."),
        ("Hero headline: 'Agents never forget — they spill over.' Keeps the slogan but expands it.",
         "Hero headline locked: 'Agents never forget — they spill over.'"),
        ("Section count: I want exactly 5. Hero, How it works, Demo, Pricing, Footer-CTA. No more.",
         "Five sections: Hero / How it works / Demo / Pricing / Footer-CTA. Tight."),
        ("Pricing-section CTA distinct from hero: 'Get the proxy'. Direct, no marketing fluff.",
         "Pricing CTA: 'Get the proxy'. Final."),
        ("Email signup placeholder: 'work@yourcompany.com'. Implies team usage, not personal.",
         "Email placeholder: 'work@yourcompany.com'."),
        ("Footer year: just 2026. No 'copyright', no '©'. Minimal.",
         "Footer year: 2026."),
    ]
    for u, a in decisions:
        turns.append({"role": "user", "content": u})
        turns.append({"role": "assistant", "content": a})

    # 30 turns of unrelated work to push the decisions out
    for i in range(15):
        turns.append({"role": "user", "content": f"sub-task {i:02d}: review the test suite, find slow tests"})
        turns.append({"role": "assistant", "content": f"reviewed batch {i:02d} of tests, two slow ones flagged in tests/integration/"})
    return turns


def _check_any(text: str, expected: list[str]) -> tuple[list[str], bool]:
    hits = [e for e in expected if e.lower() in text.lower()]
    return hits, len(hits) == 0


def _extract_text(resp: dict) -> str:
    return "".join(
        b.get("text", "")
        for b in resp.get("content", [])
        if isinstance(b, dict)
    )


def _call(base_url: str, auth: str, payload: dict, extra_headers: dict | None = None) -> dict:
    headers = {
        "Authorization": auth,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    with httpx.Client(timeout=120.0) as client:
        r = client.post(f"{base_url}/v1/messages", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()


def run_logic_check(
    history: list[dict],
    detail: DetailCheck,
    base_url: str,
    auth: str,
    model: str,
    mode: str,
    extra_headers: dict | None = None,
) -> LogicResult:
    payload = {
        "model": model,
        "max_tokens": 100,
        "messages": history + [{"role": "user", "content": detail.question}],
    }
    t0 = time.time()
    try:
        resp = _call(base_url, auth, payload, extra_headers=extra_headers)
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, missed_all = _check_any(text, detail.expected)
        return LogicResult(
            detail=detail.name, mode=mode, response=text,
            hits=hits, missed_all=missed_all,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return LogicResult(
            detail=detail.name, mode=mode, response="",
            hits=[], missed_all=True,
            input_tokens=0, output_tokens=0,
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def render_logic_report(results: list[LogicResult]) -> str:
    by_mode: dict[str, list[LogicResult]] = {}
    for r in results:
        by_mode.setdefault(r.mode, []).append(r)
    lines = ["# Landing-page logic retention", ""]
    for mode, rs in by_mode.items():
        kept = sum(1 for r in rs if not r.missed_all)
        lines.append(f"- **{mode}**: {kept}/{len(rs)} details preserved")
    lines.append("\n## per-detail\n")
    lines.append("| detail | mode | hit | missed | response |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        hit = ",".join(r.hits) if r.hits else "-"
        missed = "yes" if r.missed_all else "no"
        excerpt = (r.response or "").replace("|", "/").replace("\n", " ")[:80]
        lines.append(f"| {r.detail} | {r.mode} | {hit} | {missed} | {excerpt} |")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 2: CLI**

In `src/spillover/cli.py`:

```python
@main.command(name="bench-logic")
@click.option("--proxy-url", default="http://127.0.0.1:8787")
@click.option("--vanilla-url", default="https://api.anthropic.com")
@click.option("--report", default="bench-logic-report.md")
@click.option("--model", default="claude-haiku-4-5-20251001")
@click.option("--keep-last-n", default=8,
              help="vanilla mode: how many tail turns to keep when simulating compaction")
def bench_logic(proxy_url: str, vanilla_url: str, report: str, model: str, keep_last_n: int):
    """Run the landing-page logic-retention scenario per-detail."""
    import hashlib, json, os, uuid
    from pathlib import Path
    from spillover.bench.landing_page_scenario import (
        LANDING_PAGE_DETAILS, build_landing_page_history, render_logic_report, run_logic_check,
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
```

- [ ] **Step 3: Test (unit, no network)**

```python
from spillover.bench.landing_page_scenario import (
    LANDING_PAGE_DETAILS, LogicResult,
    _check_any, build_landing_page_history, render_logic_report,
)


def test_history_has_all_details_present():
    history = build_landing_page_history()
    full_text = " ".join(t["content"] for t in history)
    assert "#06FFB0" in full_text
    assert "Inter" in full_text
    assert "Geist" in full_text
    assert "Stop compacting" in full_text
    assert "work@yourcompany.com" in full_text
    assert "2026" in full_text


def test_history_pushes_decisions_into_evicted_region():
    history = build_landing_page_history()
    assert len(history) >= 40
    # Last 8 turns shouldn't contain the early decisions (proves they'd be lost
    # under vanilla truncation)
    tail_text = " ".join(t["content"] for t in history[-8:])
    assert "06FFB0" not in tail_text
    assert "Stop compacting" not in tail_text


def test_check_any_case_insensitive():
    hits, missed = _check_any("we use Inter for body", ["Inter", "Geist"])
    assert "Inter" in hits
    assert missed is False
    hits, missed = _check_any("hi", ["Inter"])
    assert hits == []
    assert missed is True


def test_render_logic_report_renders_per_mode_count():
    results = [
        LogicResult("d1", "vanilla_truncated", "ok", [], True, 10, 5, 100),
        LogicResult("d1", "spillover", "ok #06FFB0", ["#06FFB0"], False, 200, 5, 200),
    ]
    md = render_logic_report(results)
    assert "vanilla_truncated**: 0/1" in md
    assert "spillover**: 1/1" in md


def test_all_details_have_clear_expected():
    for d in LANDING_PAGE_DETAILS:
        assert d.expected
        assert all(isinstance(e, str) for e in d.expected)
```

- [ ] **Step 4: Run + commit**

```
python -m pytest tests/unit/test_landing_page_logic.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(bench): landing-page logic retention scenario + spillover bench-logic"
```

---

## Phase 5 — Run the real benches + analyze

### Task 6: Run `bench-long` and `bench-logic` against live Anthropic

This is operational. After all Phase 0-4 commits land:

- [ ] **Step 1: Restart proxy with realistic ceiling**

```
powershell.exe "Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
sleep 2
cd C:/Users/luiz.rs/Documents/Projects/spillover
SPILLOVER_OPERATIONAL_CEILING_TOKENS=6000 SPILLOVER_WATERMARK=0.7 SPILLOVER_LTM_PLACEMENT=between nohup spillover up > /tmp/spillover_long.log 2>&1 &
sleep 5
curl -s http://127.0.0.1:8787/health
```

- [ ] **Step 2: Run long-conversation bench**

```
spillover bench-long --report docs/eval/long-conversation-v1.6.0.md --model claude-haiku-4-5-20251001
```

- [ ] **Step 3: Run logic-retention bench**

```
spillover bench-logic --report docs/eval/landing-page-logic-v1.6.0.md --model claude-haiku-4-5-20251001
```

- [ ] **Step 4: Cleanup proxy + commit reports**

```
powershell.exe "Get-NetTCPConnection -LocalPort 8787 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
git add docs/eval/
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "eval: publish long-conversation + landing-page logic baselines (v1.6.0)"
```

- [ ] **Step 5: Write `docs/eval/analysis-v1.6.0.md`**

Document:
- Long-conversation numbers (vanilla_truncated vs spillover)
- Landing-page per-detail pass/fail table
- Which details survived, which didn't
- Plan 11 priorities based on what broke

- [ ] **Step 6: Commit analysis**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "eval: analysis-v1.6.0 — long-conv + logic retention delta"
```

---

## Phase 6 — Tag + push

### Task 7: v1.6.0 tag + push branch + merge master + push

- [ ] **Step 1: Full suite**

```
python -m pytest -v -m "not slow"
python -m ruff check src/ tests/
```

Expected: ~215 fast tests pass.

- [ ] **Step 2: Tag + push**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit --allow-empty -m "chore: spillover v1.6.0 (Plan 10 done — vision complete + logic test)"
git tag -a v1.6.0 -m "spillover v1.6.0 - placement=between, causality leg, task type, long-conv+logic benches"
git push -u origin feat/plan10-vision-complete
git push origin --tags
git checkout master
git merge --no-ff feat/plan10-vision-complete -m "Merge Plan 10: vision complete + logic retention test (v1.6.0)"
git push origin master
git push origin --tags
```

---

## Definition of Done

1. Plan 1–9 tests still pass; ~15 new tests.
2. `ruff check src/ tests/` exits 0.
3. `SPILLOVER_LTM_PLACEMENT=between` works as user originally specified.
4. Causality_chain retriever leg returns hits when AFTER edges exist.
5. `has_open_task` detects English + PT-BR pending patterns; classifier returns `task`.
6. `spillover bench-long` runs and publishes results.
7. `spillover bench-logic` runs and publishes per-detail pass/fail.
8. `v1.6.0` tag exists locally + pushed.

End of plan.
