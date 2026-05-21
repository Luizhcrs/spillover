# spillover Counter-Compaction Implementation Plan (Plan 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the 4 counter-compaction defenses from spec §5 so that spillover can keep its overflow architecture working even when client CLIs (Claude Code, Codex, etc.) try to auto-compact the conversation locally.

**Architecture:** Two of the four defenses live in the proxy itself (Vector 1 — `usage.input_tokens` rewrite on the response; Vector 4 — `/compact`-style tool-call interception). One is wrapper-side (Vector 2 — env-var injection for known CLIs). The last is detection-and-rescue (Vector 3 — track every assistant turn the proxy has seen via the `seen_turns` table from Plan 1; on every new request, diff the inbound conversation against `seen_turns`; if turns N+1 contains a "summary" message that subsumes earlier turns A,B,C from turn N, mark compaction, restore A,B,C as `rescued_from_compaction=1` episodes, log a `compaction_detected` event, and re-inject them via the LTM block on the next forward).

**Tech Stack:** No new deps. All work is on Plan 1+2 modules plus one new `counter_compact/` subpackage.

**Scope NOT covered in this plan (deferred to Plan 4):**
- OpenAI adapter
- Decay scheduler
- Wrappers for Codex / Cursor / Continue (only the Claude Code wrapper ships here as the canonical example)
- A/B benchmark
- Prometheus metrics endpoint

End state of this plan:
- Proxy rewrites `usage.input_tokens` on every response so the client believes its budget is healthier than it actually is.
- Proxy intercepts known `/compact` patterns (Claude Code's internal compact tool-call shape) and short-circuits them.
- A `spillover-cc` wrapper script ships that sets the disable env vars and points the CLI at the proxy.
- The proxy detects when a client compacts anyway by diffing inbound conversation against `seen_turns`, rescues the lost raw turns, and re-injects them via LTM.
- New telemetry: `compaction_detected_total` counter logged via the `spillover` logger.
- ~25 new tests; full suite stays green.

---

## File structure

New files:

```
src/spillover/counter_compact/
  __init__.py
  usage_rewrite.py             # Vector 1
  intercept.py                 # Vector 4
  detection.py                 # Vector 3
  env_vars.py                  # Vector 2 (data: which env vars per CLI)
src/spillover/wrappers/
  __init__.py
  cc.py                        # Claude Code wrapper script (Python entry-point)
tests/unit/
  test_usage_rewrite.py
  test_intercept.py
  test_detection.py
  test_env_vars.py
tests/integration/
  test_counter_compact_lifecycle.py
```

Modified files:

```
src/spillover/proxy/app.py     # call usage rewrite + intercept + detection
src/spillover/cli.py           # add `spillover wrap cc <args...>` subcommand
src/spillover/archive/writer.py  # accept compaction_rescued flag
pyproject.toml                 # add `spillover-cc = "spillover.wrappers.cc:main"` entry
```

Single responsibility:
- `usage_rewrite.py` — pure function `rewrite_usage(usage_dict, tokens_archived_this_turn) -> usage_dict`. Idempotent on the response JSON.
- `intercept.py` — pure function `should_intercept_request(payload) -> bool` + `make_intercept_response(payload) -> dict`. No I/O.
- `detection.py` — `record_seen_turns(db, project_id, conversation)` + `detect_compaction(db, project_id, conversation) -> list[RescuedTurn]`. The only side effect is on the per-project DB.
- `env_vars.py` — a constants dictionary mapping CLI name to env var dict. No code.
- `wrappers/cc.py` — Click entry that sets env vars and `exec`s `claude code` with appropriate `ANTHROPIC_BASE_URL`. No business logic.

---

## Phase 0 — Per-CLI env-var data

### Task 1: Define disable env vars

**Files:**
- Create: `src/spillover/counter_compact/__init__.py` (empty)
- Create: `src/spillover/counter_compact/env_vars.py`
- Create: `tests/unit/test_env_vars.py`

- [ ] **Step 1: Write `env_vars.py`**

```python
from __future__ import annotations

# Env vars known to disable client-side context compaction per CLI.
# Documented intent — actual flag names may evolve with CLI versions;
# the wrapper sets ALL of these so we cover historical variants.

CC_DISABLE_ENV: dict[str, str] = {
    "CLAUDE_CODE_AUTO_COMPACT": "0",
    "CLAUDE_CODE_DISABLE_COMPACT": "1",
    "CLAUDE_CODE_DISABLE_AUTO_COMPACT": "1",
}

CODEX_DISABLE_ENV: dict[str, str] = {
    "CODEX_DISABLE_COMPACT": "1",
}

DISABLE_ENV_BY_CLI: dict[str, dict[str, str]] = {
    "cc": CC_DISABLE_ENV,
    "claude-code": CC_DISABLE_ENV,
    "codex": CODEX_DISABLE_ENV,
}


def env_for(cli_name: str) -> dict[str, str]:
    """Return env vars to set when wrapping the named CLI."""
    return DISABLE_ENV_BY_CLI.get(cli_name, {})
```

- [ ] **Step 2: Test**

```python
from spillover.counter_compact.env_vars import env_for


def test_cc_disable_vars():
    e = env_for("cc")
    assert e["CLAUDE_CODE_AUTO_COMPACT"] == "0"
    assert e["CLAUDE_CODE_DISABLE_COMPACT"] == "1"


def test_codex_disable_vars():
    e = env_for("codex")
    assert e["CODEX_DISABLE_COMPACT"] == "1"


def test_unknown_cli_returns_empty():
    assert env_for("nonexistent") == {}
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_env_vars.py -v
```

Expected: 3 PASSED.

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(counter-compact): disable-env-vars table per CLI"
```

---

## Phase 1 — Vector 1: usage rewrite

### Task 2: rewrite_usage function

**Files:**
- Create: `src/spillover/counter_compact/usage_rewrite.py`
- Create: `tests/unit/test_usage_rewrite.py`

- [ ] **Step 1: Write `usage_rewrite.py`**

```python
from __future__ import annotations


def rewrite_usage(
    usage: dict | None,
    tokens_archived_this_turn: int,
) -> dict | None:
    """Subtract tokens_archived_this_turn from input_tokens so the client
    believes its budget is healthier and does not trigger auto-compact.

    Idempotent: returns a new dict; does not mutate input. Floors at 1 to
    avoid division-by-zero in downstream client budget heuristics.
    """
    if not usage:
        return usage
    real_input = int(usage.get("input_tokens", 0))
    new_input = max(1, real_input - max(0, tokens_archived_this_turn))
    out = dict(usage)
    out["input_tokens"] = new_input
    out["spillover_real_input_tokens"] = real_input  # for audit
    return out


def rewrite_response_json(
    resp_json: dict,
    tokens_archived_this_turn: int,
) -> dict:
    """Apply rewrite to the response's `usage` field if present."""
    if "usage" not in resp_json:
        return resp_json
    out = dict(resp_json)
    out["usage"] = rewrite_usage(resp_json["usage"], tokens_archived_this_turn)
    return out
```

- [ ] **Step 2: Test**

```python
from spillover.counter_compact.usage_rewrite import (
    rewrite_response_json,
    rewrite_usage,
)


def test_rewrite_subtracts_archived():
    u = rewrite_usage({"input_tokens": 1000, "output_tokens": 50}, 400)
    assert u["input_tokens"] == 600
    assert u["spillover_real_input_tokens"] == 1000
    assert u["output_tokens"] == 50


def test_rewrite_floors_at_1():
    u = rewrite_usage({"input_tokens": 100}, 200)
    assert u["input_tokens"] == 1


def test_rewrite_no_usage():
    assert rewrite_usage(None, 5) is None
    assert rewrite_usage({}, 5) == {}


def test_rewrite_response_json_passthrough_without_usage():
    body = {"id": "msg", "content": []}
    out = rewrite_response_json(body, 100)
    assert out == body


def test_rewrite_response_json_with_usage():
    body = {"usage": {"input_tokens": 800, "output_tokens": 100}}
    out = rewrite_response_json(body, 300)
    assert out["usage"]["input_tokens"] == 500
    # Original body untouched
    assert body["usage"]["input_tokens"] == 800
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_usage_rewrite.py -v
```

Expected: 5 PASSED.

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(counter-compact): usage_rewrite + rewrite_response_json"
```

---

## Phase 2 — Vector 4: intercept compaction requests

### Task 3: intercept module

**Files:**
- Create: `src/spillover/counter_compact/intercept.py`
- Create: `tests/unit/test_intercept.py`

- [ ] **Step 1: Write `intercept.py`**

```python
from __future__ import annotations

import time
import uuid
from typing import Any

# Known patterns that indicate the CLI is asking the model to "compact" or
# "summarize" the conversation locally. We short-circuit these so the CLI
# treats them as completed without forwarding to Anthropic.

_COMPACT_KEYWORDS = [
    "compact the conversation",
    "compact this conversation",
    "summarize the conversation so far",
    "summarize this conversation",
    "create a concise summary of the conversation",
    "resuma a conversa",
    "compacte a conversa",
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


def should_intercept_request(payload: dict) -> bool:
    """Return True if the inbound /v1/messages payload looks like the CLI is
    asking the model to compact/summarize the conversation."""
    messages = payload.get("messages") or []
    if not messages:
        return False
    last = messages[-1]
    if last.get("role") != "user":
        return False
    text = _content_to_text(last.get("content")).lower()
    return any(kw in text for kw in _COMPACT_KEYWORDS)


def make_intercept_response(payload: dict) -> dict:
    """Return a synthetic 200 response that satisfies the CLI's compact request
    without forwarding to Anthropic. The body is a no-op message hinting the
    proxy is managing memory."""
    return {
        "id": f"msg_spillover_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": payload.get("model", "claude-opus-4-7"),
        "content": [
            {
                "type": "text",
                "text": (
                    "[spillover] Conversation memory is managed by the spillover "
                    "proxy. No client-side compaction needed."
                ),
            }
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1,
            "output_tokens": 20,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "spillover_intercepted": True,
        "spillover_intercept_ts": int(time.time() * 1000),
    }
```

- [ ] **Step 2: Test**

```python
from spillover.counter_compact.intercept import (
    make_intercept_response,
    should_intercept_request,
)


def test_intercept_detects_english_compact():
    payload = {
        "messages": [
            {"role": "user", "content": "Please compact the conversation so far"}
        ]
    }
    assert should_intercept_request(payload) is True


def test_intercept_detects_ptbr_compact():
    payload = {
        "messages": [
            {"role": "user", "content": "Resuma a conversa para liberar contexto"}
        ]
    }
    assert should_intercept_request(payload) is True


def test_intercept_ignores_normal_message():
    payload = {
        "messages": [{"role": "user", "content": "fix the bug in auth"}]
    }
    assert should_intercept_request(payload) is False


def test_intercept_ignores_assistant_last_turn():
    payload = {
        "messages": [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "compact the conversation"},
        ]
    }
    assert should_intercept_request(payload) is False


def test_intercept_empty_messages():
    assert should_intercept_request({"messages": []}) is False
    assert should_intercept_request({}) is False


def test_intercept_with_content_blocks():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "please summarize this conversation"}
                ],
            }
        ]
    }
    assert should_intercept_request(payload) is True


def test_make_intercept_response_shape():
    r = make_intercept_response({"model": "claude-opus-4-7"})
    assert r["role"] == "assistant"
    assert r["model"] == "claude-opus-4-7"
    assert r["spillover_intercepted"] is True
    assert r["content"][0]["type"] == "text"
    assert "spillover" in r["content"][0]["text"]
    assert r["stop_reason"] == "end_turn"
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_intercept.py -v
```

Expected: 7 PASSED.

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(counter-compact): detect + intercept compact/summarize requests"
```

---

## Phase 3 — Vector 3: conversation-diff detection + rescue

### Task 4: detection module

**Files:**
- Create: `src/spillover/counter_compact/detection.py`
- Modify: `src/spillover/archive/writer.py` (already accepts content; add an optional `compaction_rescued: bool` flag to `Turn` so rescued episodes get marked)
- Create: `tests/unit/test_detection.py`

This is the most subtle defense. The proxy stores every assistant turn it has seen via `seen_turns`. On every inbound request:

1. Hash every prior `assistant` message in the conversation.
2. Compare against `seen_turns` for this project.
3. If a previously-seen sequence A,B,C is now absent and replaced by a single message that looks like a summary (short + lacks tool calls + appears at the position A,B,C used to be), record `compaction_detected`.
4. Rescue A,B,C as new `episodes` rows with `compaction_rescued=1`.

The test exercises a synthetic two-request flow: request 1 with messages A,B,C,D; request 2 where A,B,C are collapsed into a summary "S".

- [ ] **Step 1: Modify `src/spillover/archive/writer.py`**

Add `compaction_rescued: bool = False` to the `Turn` dataclass (insert after `ts: int = 0`):

```python
    compaction_rescued: bool = False
```

Then update `archive_raw` to include the new column in INSERT. Change the INSERT statement to:

```python
        db.execute(
            """
            INSERT INTO episodes (
                id, project_id, role, content_json, tool_calls_json,
                code_refs_json, token_count, ts, hash, compaction_rescued
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if turn.compaction_rescued else 0,
            ),
        )
```

- [ ] **Step 2: Write `src/spillover/counter_compact/detection.py`**

```python
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from spillover.logging import get_logger

log = get_logger("counter_compact")


@dataclass(frozen=True)
class SeenTurn:
    turn_hash: str
    turn_index: int
    content_json: str
    first_seen_ts: int
    last_seen_ts: int


@dataclass(frozen=True)
class RescuedTurn:
    role: str
    content: Any
    token_count: int
    original_hash: str


def _hash_assistant_message(msg: dict) -> str:
    payload = json.dumps(
        {"role": msg.get("role"), "content": msg.get("content")},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_seen_turns(
    db: sqlite3.Connection,
    project_id: str,
    messages: list[dict],
) -> None:
    """Upsert every assistant message in the conversation into seen_turns.

    Keeps last_seen_ts current so we can prune stale rows later.
    """
    now = int(time.time() * 1000)
    for idx, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        h = _hash_assistant_message(msg)
        content_json = json.dumps(msg.get("content"), ensure_ascii=False)
        existing = db.execute(
            "SELECT first_seen_ts FROM seen_turns "
            "WHERE project_id=? AND turn_hash=?",
            (project_id, h),
        ).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO seen_turns(project_id, turn_hash, turn_index, "
                "content_json, first_seen_ts, last_seen_ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, h, idx, content_json, now, now),
            )
        else:
            db.execute(
                "UPDATE seen_turns SET last_seen_ts=? "
                "WHERE project_id=? AND turn_hash=?",
                (now, project_id, h),
            )


def detect_compaction(
    db: sqlite3.Connection,
    project_id: str,
    messages: list[dict],
) -> list[RescuedTurn]:
    """Compare the current inbound messages against seen_turns.

    Returns the list of assistant turns the proxy previously witnessed that
    have now disappeared from the conversation, ordered by their original
    turn_index.
    """
    current_hashes: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        current_hashes.add(_hash_assistant_message(msg))

    rows = db.execute(
        "SELECT turn_hash, turn_index, content_json FROM seen_turns "
        "WHERE project_id=? ORDER BY turn_index ASC",
        (project_id,),
    ).fetchall()

    rescued: list[RescuedTurn] = []
    for row in rows:
        if row["turn_hash"] in current_hashes:
            continue
        # This previously-seen turn is missing -> compaction suspected.
        content = json.loads(row["content_json"])
        # crude token count: char/4 like the heuristic tokenizer
        from spillover.eviction.tokenizer import count_tokens
        rescued.append(
            RescuedTurn(
                role="assistant",
                content=content,
                token_count=count_tokens(content),
                original_hash=row["turn_hash"],
            )
        )

    if rescued:
        log.warning(
            "compaction_detected project=%s rescued_count=%d",
            project_id,
            len(rescued),
        )

    return rescued


def prune_old_seen_turns(
    db: sqlite3.Connection,
    project_id: str,
    ttl_hours: int = 72,
) -> int:
    """Delete seen_turns rows not refreshed within ttl_hours. Returns count."""
    cutoff = int(time.time() * 1000) - ttl_hours * 3600 * 1000
    cur = db.execute(
        "DELETE FROM seen_turns WHERE project_id=? AND last_seen_ts < ?",
        (project_id, cutoff),
    )
    return cur.rowcount
```

- [ ] **Step 3: Test**

`tests/unit/test_detection.py`:

```python
from spillover.counter_compact.detection import (
    detect_compaction,
    prune_old_seen_turns,
    record_seen_turns,
)
from spillover.storage.sqlite import open_project_db


def _msg(role, text):
    return {"role": role, "content": text}


def test_record_seen_turns_upserts(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        msgs = [
            _msg("user", "u1"),
            _msg("assistant", "a1"),
            _msg("user", "u2"),
            _msg("assistant", "a2"),
        ]
        record_seen_turns(db, "p1", msgs)
        rows = db.execute(
            "SELECT COUNT(*) FROM seen_turns WHERE project_id=?", ("p1",)
        ).fetchone()
        assert rows[0] == 2  # 2 assistant turns
        # Re-record: last_seen_ts updates, no duplicates
        record_seen_turns(db, "p1", msgs)
        rows = db.execute(
            "SELECT COUNT(*) FROM seen_turns WHERE project_id=?", ("p1",)
        ).fetchone()
        assert rows[0] == 2
    finally:
        db.close()


def test_detect_compaction_finds_missing(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        # Turn 1: see a1, a2, a3
        msgs_t1 = [
            _msg("assistant", "a1"),
            _msg("assistant", "a2"),
            _msg("assistant", "a3"),
        ]
        record_seen_turns(db, "p1", msgs_t1)
        # Turn 2: only a4 (CLI compacted a1+a2+a3 into something else)
        msgs_t2 = [_msg("assistant", "a4")]
        rescued = detect_compaction(db, "p1", msgs_t2)
        assert len(rescued) == 3
        contents = [r.content for r in rescued]
        assert "a1" in contents
        assert "a2" in contents
        assert "a3" in contents
    finally:
        db.close()


def test_detect_compaction_no_loss(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        msgs = [_msg("assistant", "a1"), _msg("assistant", "a2")]
        record_seen_turns(db, "p1", msgs)
        rescued = detect_compaction(db, "p1", msgs)
        assert rescued == []
    finally:
        db.close()


def test_prune_old_seen_turns(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        record_seen_turns(db, "p1", [_msg("assistant", "old")])
        # Manually backdate last_seen_ts
        db.execute("UPDATE seen_turns SET last_seen_ts=0 WHERE project_id=?", ("p1",))
        deleted = prune_old_seen_turns(db, "p1", ttl_hours=1)
        assert deleted == 1
    finally:
        db.close()
```

- [ ] **Step 4: Update writer test to cover `compaction_rescued` column**

Append to `tests/unit/test_archive_writer.py`:

```python


def test_archive_raw_compaction_rescued_flag(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        turn = Turn(
            project_id="p1",
            role="assistant",
            content="rescued",
            tool_calls=[],
            code_refs=[],
            token_count=2,
            ts=1,
            compaction_rescued=True,
        )
        eid = archive_raw(db, turn)
        row = db.execute(
            "SELECT compaction_rescued FROM episodes WHERE id = ?", (eid,)
        ).fetchone()
        assert row["compaction_rescued"] == 1
    finally:
        db.close()
```

- [ ] **Step 5: Run**

```
python -m pytest tests/unit/test_detection.py tests/unit/test_archive_writer.py -v
```

Expected: 4 + 4 = 8 PASSED.

- [ ] **Step 6: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(counter-compact): seen_turns recorder + compaction detector + rescue"
```

---

## Phase 4 — Wire into proxy

### Task 5: Integrate all 3 defenses into the proxy

**Files:**
- Modify: `src/spillover/proxy/app.py`
- Create: `tests/integration/test_counter_compact_lifecycle.py`

Add to the route handler, in order:

1. After parsing `payload`, BEFORE retrieval: if `should_intercept_request(payload)`, return `JSONResponse(make_intercept_response(payload), status_code=200)` immediately.
2. After retrieval / before forward: `record_seen_turns(db, project_id, payload["messages"])`. Also run `detect_compaction(...)`; if it returns rescued turns, archive them with `compaction_rescued=True`, enqueue facet events, and prepend them to the LTM block (concatenated).
3. On the response path, after extracting upstream `usage`, run `rewrite_response_json(resp_json, tokens_archived_this_turn)` before returning to client. `tokens_archived_this_turn` = sum of `token_count` for the episodes we archived this request, computed inside `_maybe_evict` (it already knows; just return that sum alongside the id list).

- [ ] **Step 1: Modify `_maybe_evict` to also return `tokens_archived`**

Change its return signature from `list[str]` to `tuple[list[str], int]`. The tokens_archived = sum of `turn.token_count` for the episodes you inserted. Update the route to handle the tuple.

- [ ] **Step 2: Add the new helpers + wiring**

Inside `messages` route, add at the very top of the body (right after `project_id = request.state.project_id`):

```python
        from spillover.counter_compact.intercept import (
            make_intercept_response,
            should_intercept_request,
        )
        from spillover.counter_compact.detection import (
            detect_compaction,
            record_seen_turns,
        )
        from spillover.counter_compact.usage_rewrite import rewrite_response_json

        if should_intercept_request(payload):
            log.info("intercept compact project=%s", project_id)
            return JSONResponse(make_intercept_response(payload), status_code=200)
```

After the `_inject_ltm(payload, ltm_text)` line, add detection + rescue:

```python
        # Detect compaction by diffing inbound against seen_turns
        rescue_db = open_project_db(config.db_root, project_id)
        try:
            rescued = detect_compaction(rescue_db, project_id, payload.get("messages") or [])
            # Always re-record current assistant turns afterwards
            record_seen_turns(rescue_db, project_id, payload.get("messages") or [])
        finally:
            rescue_db.close()

        if rescued:
            rescue_db = open_project_db(config.db_root, project_id)
            try:
                rescue_ids: list[str] = []
                ts = int(time.time() * 1000)
                for r in rescued:
                    eid = archive_raw(
                        rescue_db,
                        Turn(
                            project_id=project_id,
                            role=r.role,
                            content=r.content,
                            tool_calls=[],
                            code_refs=[],
                            token_count=r.token_count,
                            ts=ts,
                            compaction_rescued=True,
                        ),
                    )
                    rescue_ids.append(eid)
                if rescue_ids:
                    placeholders = ",".join("?" for _ in rescue_ids)
                    rescue_db.execute(
                        f"UPDATE episodes SET evicted=1, compaction_rescued=1 "
                        f"WHERE id IN ({placeholders})",
                        rescue_ids,
                    )
                    _enqueue_facets(app, project_id, rescue_ids, config)
            finally:
                rescue_db.close()
```

After both `_maybe_evict` calls (non-streaming and streaming), capture the `tokens_archived` and rewrite the response JSON. For non-streaming:

```python
            archived_ids, tokens_archived = _maybe_evict(...)
            ...
            resp_json = json.loads(resp_bytes)
            if tokens_archived > 0:
                resp_json = rewrite_response_json(resp_json, tokens_archived)
                resp_bytes = json.dumps(resp_json).encode("utf-8")
```

For streaming, the SSE rewrite is more invasive — the simpler approach is to only rewrite the final `message_stop` event's usage. To keep this plan tractable, **scope Plan 3's usage rewrite to non-streaming only** and add a TODO for streaming rewrite in Plan 4.

- [ ] **Step 3: Integration test**

`tests/integration/test_counter_compact_lifecycle.py`:

```python
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


@respx.mock
def test_intercept_short_circuits_compact_request(client, config):
    """A user message asking for compaction is intercepted and never forwarded."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_resp(10, 10)
    )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "abcdef12", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "compact the conversation so far"}
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("spillover_intercepted") is True
    assert route.call_count == 0  # never forwarded


@respx.mock
def test_usage_rewrite_subtracts_archived(client, config):
    """Non-streaming response usage.input_tokens is rewritten when eviction archived."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_resp(900, 80)
    )
    pid = "abcdef12"
    messages = []
    for i in range(12):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": "x" * 320})
    r = client.post(
        "/v1/messages",
        headers={"X-Project": pid, "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": messages,
        },
    )
    assert r.status_code == 200
    body = r.json()
    usage = body["usage"]
    assert "spillover_real_input_tokens" in usage
    assert usage["spillover_real_input_tokens"] == 900
    assert usage["input_tokens"] < 900  # subtracted


@respx.mock
def test_compaction_detection_rescues_dropped_turns(client, config):
    """Two-request flow: round-trip 1 sees assistant turns; round-trip 2 sends
    a 'summary' that drops them. Proxy rescues the missing turns."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_resp(50, 50)
    )
    pid = "abcdef12"
    # Round-trip 1: substantial history
    r1 = client.post(
        "/v1/messages",
        headers={"X-Project": pid, "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1 about foo.py"},
                {"role": "user", "content": "u2"},
                {"role": "assistant", "content": "a2 about bar.py"},
                {"role": "user", "content": "u3"},
            ],
        },
    )
    assert r1.status_code == 200

    # Round-trip 2: client compacts a1+a2 into a summary
    r2 = client.post(
        "/v1/messages",
        headers={"X-Project": pid, "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [
                {"role": "assistant", "content": "SUMMARY: discussed foo.py and bar.py"},
                {"role": "user", "content": "now do the thing"},
            ],
        },
    )
    assert r2.status_code == 200

    # The proxy should have archived a1 and a2 as rescued episodes
    db = open_project_db(config.db_root, pid)
    try:
        rescued_count = db.execute(
            "SELECT COUNT(*) FROM episodes WHERE compaction_rescued=1"
        ).fetchone()[0]
        assert rescued_count == 2
    finally:
        db.close()
```

- [ ] **Step 4: Run**

```
python -m pytest tests/integration/test_counter_compact_lifecycle.py -v
```

Expected: 3 PASSED.

```
python -m pytest -v -m "not slow"
```

Expected: 107 PASSED (92 from Plan 2 fast + 15 new).

```
python -m pytest -v
```

Expected: 112 PASSED.

```
python -m ruff check src/ tests/
```

Expected: 0 errors.

- [ ] **Step 5: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(proxy): wire intercept + detection + usage rewrite (non-streaming)"
```

No Co-Authored-By trailer.

---

## Phase 5 — Wrapper script (Vector 2)

### Task 6: `spillover-cc` wrapper

**Files:**
- Create: `src/spillover/wrappers/__init__.py` (empty)
- Create: `src/spillover/wrappers/cc.py`
- Modify: `pyproject.toml` (add entry point)
- Create: `tests/unit/test_wrapper_cc.py`

- [ ] **Step 1: Write `src/spillover/wrappers/cc.py`**

```python
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import click

from spillover.config import Config
from spillover.counter_compact.env_vars import env_for


@click.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.option(
    "--proxy",
    default=None,
    help="Override ANTHROPIC_BASE_URL (defaults to http://127.0.0.1:<port>)",
)
@click.option("--project", default=None, help="Override X-Project header value")
@click.pass_context
def main(ctx, proxy: str | None, project: str | None):
    """Launch Claude Code with spillover wired in.

    Sets ANTHROPIC_BASE_URL, the disable-compact env vars, and X-Project,
    then exec's `claude code` with any remaining args.
    """
    config = Config.from_env()
    cwd = Path.cwd().resolve()
    project_id = project or hashlib.sha1(str(cwd).encode("utf-8")).hexdigest()

    proxy_url = proxy or f"http://127.0.0.1:{config.port}"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = proxy_url
    env.update(env_for("cc"))
    # Note: claude-code does not currently accept custom request headers, so
    # X-Project must be injected another way. For now we export it as an env
    # var that user-side hooks can read; the canonical path goes through a
    # small HTTP client that does support custom headers.
    env["SPILLOVER_PROJECT_ID"] = project_id

    cmd = ["claude", "code", *ctx.args]
    click.echo(
        f"spillover-cc: ANTHROPIC_BASE_URL={proxy_url} "
        f"X-Project(env SPILLOVER_PROJECT_ID)={project_id}"
    )
    completed = subprocess.run(cmd, env=env, check=False)
    sys.exit(completed.returncode)
```

- [ ] **Step 2: Modify `pyproject.toml`**

Under `[project.scripts]` add a second entry alongside the existing `spillover`:

```toml
[project.scripts]
spillover = "spillover.cli:main"
spillover-cc = "spillover.wrappers.cc:main"
```

- [ ] **Step 3: Test**

`tests/unit/test_wrapper_cc.py`:

```python
import sys
from unittest.mock import patch

from click.testing import CliRunner

from spillover.wrappers.cc import main


def test_wrapper_passes_proxy_url(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_PORT", "9999")
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    runner = CliRunner()

    captured = {}

    def _fake_run(cmd, env, check):
        captured["env"] = env
        captured["cmd"] = cmd

        class _R:
            returncode = 0

        return _R()

    with patch("spillover.wrappers.cc.subprocess.run", side_effect=_fake_run):
        with patch.object(sys, "exit") as _mock_exit:
            result = runner.invoke(main, ["--project", "proj-test", "--help-claude-code"])

    assert "ANTHROPIC_BASE_URL" in captured["env"]
    assert captured["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999"
    assert captured["env"]["CLAUDE_CODE_AUTO_COMPACT"] == "0"
    assert captured["env"]["SPILLOVER_PROJECT_ID"] == "proj-test"
    assert result.exit_code == 0


def test_wrapper_default_project_is_cwd_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    runner = CliRunner()

    captured = {}

    def _fake_run(cmd, env, check):
        captured["env"] = env

        class _R:
            returncode = 0

        return _R()

    with patch("spillover.wrappers.cc.subprocess.run", side_effect=_fake_run):
        with patch.object(sys, "exit"):
            runner.invoke(main)

    assert "SPILLOVER_PROJECT_ID" in captured["env"]
    pid = captured["env"]["SPILLOVER_PROJECT_ID"]
    assert len(pid) == 40  # sha1 hex
```

- [ ] **Step 4: Run + reinstall**

```
python -m pip install -e ".[dev]"
python -m pytest tests/unit/test_wrapper_cc.py -v
```

Expected: 2 PASSED. `which spillover-cc` should resolve.

- [ ] **Step 5: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(wrapper): spillover-cc launcher for Claude Code with disable env"
```

No Co-Authored-By.

---

## Phase 6 — Verify + tag

### Task 7: Final suite + tag v0.3.0

- [ ] **Step 1: Full suite**

```
python -m pytest -v -m "not slow"
```

Expected: 110+ PASSED.

```
python -m pytest -v
```

Expected: ~115 PASSED.

- [ ] **Step 2: Ruff**

```
python -m ruff check src/ tests/
```

Expected: 0 errors.

- [ ] **Step 3: Tag**

```
git tag -a v0.3.0 -m "spillover v0.3.0 - counter-compaction (Plan 3)"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit --allow-empty -m "chore: spillover counter-compaction complete (Plan 3 done)"
```

---

## Definition of Done

1. All Plan 1 + Plan 2 + Plan 3 tests pass (≥110 fast, ≥115 with slow).
2. `ruff check src/ tests/` exits 0.
3. Intercept short-circuits a compact request without forwarding upstream.
4. Non-streaming response usage gets `spillover_real_input_tokens` field and a reduced `input_tokens`.
5. Two-request integration test shows `compaction_rescued=1` rows for turns that disappeared.
6. `spillover-cc` resolves on PATH and sets the right env vars.
7. `v0.3.0` tag exists.
8. All commits authored by `luizhcrs <luizhcrs@gmail.com>`, no `Co-Authored-By` trailers.

## Deferred to Plan 4

- Streaming SSE usage rewrite (current Plan 3 handles non-streaming only).
- OpenAI adapter.
- Decay scheduler.
- Wrappers for Codex / Cursor / Continue (only `spillover-cc` ships in Plan 3).
- Prometheus metrics endpoint.
- A/B benchmark.

End of plan.
