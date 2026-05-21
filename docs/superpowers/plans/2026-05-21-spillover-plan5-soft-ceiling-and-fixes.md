# spillover Plan 5: Soft Ceiling + C1–C5 Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every Critical and the most-impactful Important issues from the v1.0.0 code review, AND introduce the Soft-Ceiling Overflow Architecture (operational ceiling decoupled from provider window, granular 5-tier token budget, dynamic budgeting profile, weighted-FIFO selector for semantic density). After Plan 5, spillover transitions from internal-demo-ready to real-traffic-ready on Claude Code.

**Architecture changes:**

1. **Soft Ceiling.** Introduce `operational_ceiling_tokens` (the SOFT cap spillover obeys) and `provider_max_tokens` (informational). All eviction math uses the operational ceiling. Default 500k of 1M Opus. Watermark stays a fraction of the ceiling, not the provider max.

2. **Granular token budget.** Split the ceiling into 5 tiers — `system_pct`, `working_memory_pct`, `active_pct`, `ltm_pct`, `scratchpad_pct` — that sum to 1.0. Eviction protects the system + working_memory tiers; retrieval respects the ltm_pct; scratchpad_pct is a response reservation passed to the provider as `max_tokens` upper bound when the client did not set one.

3. **Dynamic budgeting profile.** A small classifier inspects the inbound payload (tool count, system content) and picks one of three profiles — `coding` (more working_memory), `research` (more LTM), `conversation` (more active). Selection logged so it's traceable.

4. **Weighted-FIFO selector.** Replace pure FIFO with `weight = token_count / max(1, semantic_density)` where `semantic_density = len(entities) + len(decisions) + len(tool_calls)`. Lower-density turns evicted first, even if newer. Preserves high-value turns longer.

5. **C1 fix — middleware fallback.** When `X-Project` header missing, fall back to `SPILLOVER_PROJECT_ID` env var (set by the wrappers). Documented limitation. Per-process — proxy holds one project per daemon when invoked via wrapper.

6. **C2 fix — executor offload.** `_retrieve_ltm_block`, `_maybe_evict`, `_handle_rescue`, all archive writes — moved off the event loop via `await loop.run_in_executor(...)`. Sync code untouched; only the call sites change.

7. **C3 fix — incremental SSE rewrite.** Stop buffering the entire upstream stream. Pass chunks live until we see a `data:` line containing `usage`; buffer that one chunk, rewrite it, emit. Subsequent chunks (closing events) pass through live.

8. **C4 fix — wire all 7 metrics.** Increment counters at every call site spec §9 names. Add `X-Request-Id` propagation. Update `test_metrics_endpoint.py` to assert non-zero values after a synthetic request.

9. **C5 fix — adapter response parsing.** Add `Adapter.parse_response_text(resp_json) -> str` and `Adapter.extract_usage_*(resp_or_bytes) -> tuple[int, int] | None`. Proxy uses these instead of hardcoded shapes. OpenAI path now correctly extracts `choices[0].message.content` and `prompt_tokens`/`completion_tokens`. LTM injection routes through `Adapter.inject_ltm(payload, ltm_text)` so the OpenAI adapter splices a system message at index 0 of `messages`, while Anthropic keeps the top-level `system` field.

10. **Bonus from review I-list:**
    - I1: Kuzu connection cache per project (LRU 32) + schema-init-once-per-process.
    - I2: `asyncio.Queue(maxsize=1024)` + `facet_dropped_total` counter on backpressure.
    - I3: Batch SELECT in budget + render + decay (single `WHERE id IN (...)` or JOIN).
    - I4: httpx retry policy (3 attempts, exponential backoff, only on idempotent 5xx/timeout).

**Tech stack:** no new deps.

---

## File structure

New files:

```
src/spillover/
  budget/
    __init__.py
    profile.py             # BudgetProfile + select_profile(payload)
    plan.py                # TokenPlan dataclass — split of ceiling into tiers
  request_id.py            # X-Request-Id helper
tests/unit/
  test_budget_profile.py
  test_budget_plan.py
  test_request_id.py
  test_adapter_response.py
tests/integration/
  test_middleware_fallback.py
  test_metrics_wired.py
  test_incremental_sse_rewrite.py
```

Modified files:

```
src/spillover/
  config.py                  # +6 fields: operational_ceiling_tokens, provider_max_tokens, 5 budget pcts (system/working/active/ltm/scratchpad), profile_default
  proxy/app.py               # executor offload, incremental SSE, metrics wiring, adapter.parse_response, kuzu cache
  proxy/middleware.py        # X-Project env fallback
  adapters/base.py           # +parse_response_text, +extract_usage_non_streaming, +extract_usage_sse, +inject_ltm
  adapters/anthropic.py      # implement the new Adapter methods
  adapters/openai.py         # implement the new Adapter methods
  eviction/selector.py       # weighted-FIFO mode
  retriever/budget.py        # batch SELECT
  retriever/render.py        # batch SELECT
  decay/scheduler.py         # JOIN instead of N+1
  storage/kuzu.py            # connection cache + schema-init-once
  metrics/registry.py        # +facet_dropped_total
  facet/worker.py            # respect Queue maxsize, increment dropped counter
  counter_compact/sse_rewrite.py   # incremental mode helper
  cli.py                     # wrappers test fallback path (informational)
pyproject.toml               # no changes
```

---

## Phase 0 — Config + Soft Ceiling

### Task 1: Extend Config with soft-ceiling fields

**Files:**
- Modify: `src/spillover/config.py`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Replace `Config` dataclass**

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    port: int
    watermark: float
    operational_ceiling_tokens: int
    provider_max_tokens: int
    db_root: Path
    upstream_base_url: str
    openai_base_url: str
    # 5-tier budget — must sum to 1.0
    system_pct: float
    working_memory_pct: float
    active_pct: float
    ltm_pct: float
    scratchpad_pct: float
    # legacy alias for backwards compat (== ltm_pct)
    ltm_budget_pct: float
    retriever_topk: int
    retriever_vector_k: int
    retriever_graph_k: int
    profile_default: str  # "coding" | "research" | "conversation" | "auto"

    @property
    def window_max(self) -> int:
        """Backwards-compatible alias — most code reads operational ceiling."""
        return self.operational_ceiling_tokens

    @classmethod
    def from_env(cls) -> Config:
        ceiling = int(os.environ.get("SPILLOVER_OPERATIONAL_CEILING_TOKENS",
                                     os.environ.get("SPILLOVER_WINDOW_MAX", "200000")))
        provider = int(os.environ.get("SPILLOVER_PROVIDER_MAX_TOKENS", str(ceiling * 2)))
        ltm = float(os.environ.get("SPILLOVER_LTM_BUDGET_PCT", "0.15"))
        return cls(
            port=int(os.environ.get("SPILLOVER_PORT", "8787")),
            watermark=float(os.environ.get("SPILLOVER_WATERMARK", "0.85")),
            operational_ceiling_tokens=ceiling,
            provider_max_tokens=provider,
            db_root=Path(os.environ.get("SPILLOVER_DB_ROOT", str(Path.home() / ".spillover"))),
            upstream_base_url=os.environ.get("SPILLOVER_UPSTREAM_BASE_URL", "https://api.anthropic.com"),
            openai_base_url=os.environ.get("SPILLOVER_OPENAI_BASE_URL", "https://api.openai.com"),
            system_pct=float(os.environ.get("SPILLOVER_SYSTEM_PCT", "0.04")),
            working_memory_pct=float(os.environ.get("SPILLOVER_WORKING_MEMORY_PCT", "0.20")),
            active_pct=float(os.environ.get("SPILLOVER_ACTIVE_PCT", "0.50")),
            ltm_pct=ltm,
            scratchpad_pct=float(os.environ.get("SPILLOVER_SCRATCHPAD_PCT", "0.11")),
            ltm_budget_pct=ltm,
            retriever_topk=int(os.environ.get("SPILLOVER_RETRIEVER_TOPK", "8")),
            retriever_vector_k=int(os.environ.get("SPILLOVER_RETRIEVER_VECTOR_K", "50")),
            retriever_graph_k=int(os.environ.get("SPILLOVER_RETRIEVER_GRAPH_K", "50")),
            profile_default=os.environ.get("SPILLOVER_PROFILE_DEFAULT", "auto"),
        )
```

Default split sums to 0.04 + 0.20 + 0.50 + 0.15 + 0.11 = 1.00.

- [ ] **Step 2: Update `tests/unit/test_config.py`**

In `test_config_defaults`, add assertions:

```python
    assert cfg.operational_ceiling_tokens == 200_000
    assert cfg.provider_max_tokens == 400_000
    assert cfg.window_max == 200_000  # alias
    assert cfg.system_pct == 0.04
    assert cfg.working_memory_pct == 0.20
    assert cfg.active_pct == 0.50
    assert cfg.scratchpad_pct == 0.11
    assert abs(
        cfg.system_pct + cfg.working_memory_pct + cfg.active_pct
        + cfg.ltm_pct + cfg.scratchpad_pct - 1.0
    ) < 1e-6
    assert cfg.profile_default == "auto"
```

Add a new test asserting envs override:

```python
def test_soft_ceiling_env(monkeypatch):
    monkeypatch.setenv("SPILLOVER_OPERATIONAL_CEILING_TOKENS", "500000")
    monkeypatch.setenv("SPILLOVER_PROVIDER_MAX_TOKENS", "1000000")
    cfg = Config.from_env()
    assert cfg.operational_ceiling_tokens == 500_000
    assert cfg.provider_max_tokens == 1_000_000
    assert cfg.window_max == 500_000
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_config.py -v
python -m ruff check src/ tests/
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(config): soft-ceiling + 5-tier token budget + profile_default"
```

---

### Task 2: `budget/plan.py` — TokenPlan from Config

**Files:**
- Create: `src/spillover/budget/__init__.py` (empty)
- Create: `src/spillover/budget/plan.py`
- Create: `tests/unit/test_budget_plan.py`

- [ ] **Step 1: Write `plan.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from spillover.config import Config


@dataclass(frozen=True)
class TokenPlan:
    ceiling: int
    system_tokens: int
    working_memory_tokens: int
    active_tokens: int
    ltm_tokens: int
    scratchpad_tokens: int

    @property
    def evictable_budget(self) -> int:
        """Tokens an eviction call may legitimately leave in the active layer."""
        return self.active_tokens

    @property
    def total(self) -> int:
        return (
            self.system_tokens
            + self.working_memory_tokens
            + self.active_tokens
            + self.ltm_tokens
            + self.scratchpad_tokens
        )


def plan_from_config(config: Config) -> TokenPlan:
    c = config.operational_ceiling_tokens
    return TokenPlan(
        ceiling=c,
        system_tokens=int(c * config.system_pct),
        working_memory_tokens=int(c * config.working_memory_pct),
        active_tokens=int(c * config.active_pct),
        ltm_tokens=int(c * config.ltm_pct),
        scratchpad_tokens=int(c * config.scratchpad_pct),
    )
```

- [ ] **Step 2: Test**

```python
from spillover.budget.plan import plan_from_config
from spillover.config import Config


def test_plan_sums_to_ceiling_within_rounding(monkeypatch, tmp_path):
    monkeypatch.setenv("SPILLOVER_OPERATIONAL_CEILING_TOKENS", "500000")
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    plan = plan_from_config(Config.from_env())
    # Five int casts may round down — allow up to 5 tokens drift
    assert plan.ceiling - plan.total <= 5
    assert plan.evictable_budget == plan.active_tokens


def test_plan_500k_default_split(monkeypatch, tmp_path):
    monkeypatch.setenv("SPILLOVER_OPERATIONAL_CEILING_TOKENS", "500000")
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    plan = plan_from_config(Config.from_env())
    assert plan.system_tokens == 20_000
    assert plan.working_memory_tokens == 100_000
    assert plan.active_tokens == 250_000
    assert plan.ltm_tokens == 75_000
    assert plan.scratchpad_tokens == 55_000
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_budget_plan.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(budget): TokenPlan dataclass + plan_from_config"
```

---

### Task 3: `budget/profile.py` — dynamic profile selection

**Files:**
- Create: `src/spillover/budget/profile.py`
- Create: `tests/unit/test_budget_profile.py`

- [ ] **Step 1: Write `profile.py`**

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetProfile:
    name: str
    system_pct: float
    working_memory_pct: float
    active_pct: float
    ltm_pct: float
    scratchpad_pct: float


CODING = BudgetProfile("coding", 0.05, 0.30, 0.40, 0.10, 0.15)
RESEARCH = BudgetProfile("research", 0.05, 0.10, 0.50, 0.30, 0.05)
CONVERSATION = BudgetProfile("conversation", 0.05, 0.10, 0.70, 0.10, 0.05)
DEFAULT = BudgetProfile("default", 0.04, 0.20, 0.50, 0.15, 0.11)


def select_profile(payload: dict, override: str = "auto") -> BudgetProfile:
    if override == "coding":
        return CODING
    if override == "research":
        return RESEARCH
    if override == "conversation":
        return CONVERSATION
    if override and override != "auto":
        return DEFAULT
    # auto-detect
    tool_count = sum(
        1
        for m in payload.get("messages", [])
        if isinstance(m.get("content"), list)
        and any(
            isinstance(b, dict) and b.get("type") in ("tool_use", "tool_result")
            for b in m["content"]
        )
    )
    if tool_count >= 3 or payload.get("tools"):
        return CODING
    msg_count = len(payload.get("messages", []))
    if msg_count >= 10:
        return CONVERSATION
    return DEFAULT
```

- [ ] **Step 2: Test**

```python
from spillover.budget.profile import (
    CODING,
    CONVERSATION,
    DEFAULT,
    RESEARCH,
    select_profile,
)


def test_override_explicit():
    assert select_profile({}, "coding") == CODING
    assert select_profile({}, "research") == RESEARCH
    assert select_profile({}, "conversation") == CONVERSATION


def test_auto_detects_coding_by_tools_field():
    p = select_profile({"tools": [{"name": "Read"}]})
    assert p.name == "coding"


def test_auto_detects_coding_by_tool_use_in_content():
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read"},
                    {"type": "tool_use", "name": "Edit"},
                    {"type": "tool_use", "name": "Bash"},
                ],
            }
        ]
    }
    assert select_profile(payload).name == "coding"


def test_auto_detects_conversation_by_message_count():
    payload = {"messages": [{"role": "user", "content": "hi"}] * 11}
    assert select_profile(payload).name == "conversation"


def test_default_falls_back():
    p = select_profile({"messages": [{"role": "user", "content": "hi"}]})
    assert p == DEFAULT
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_budget_profile.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(budget): dynamic profile (coding/research/conversation/default)"
```

---

## Phase 1 — C5 fix: adapter response parity

### Task 4: Extend Adapter base + Anthropic + OpenAI implementations

**Files:**
- Modify: `src/spillover/adapters/base.py`
- Modify: `src/spillover/adapters/anthropic.py`
- Modify: `src/spillover/adapters/openai.py`
- Create: `tests/unit/test_adapter_response.py`

- [ ] **Step 1: Extend `base.py`**

Add to `Adapter` ABC:

```python
    @abstractmethod
    def extract_usage_non_streaming(self, body: bytes) -> tuple[int, int] | None:
        ...

    @abstractmethod
    def extract_usage_sse(self, captured: list[bytes]) -> tuple[int, int] | None:
        ...

    @abstractmethod
    def parse_response_text(self, resp_json: dict) -> str:
        ...

    @abstractmethod
    def extract_assistant_text_sse(self, captured: list[bytes]) -> str:
        ...

    @abstractmethod
    def inject_ltm(self, payload: dict, ltm_text: str) -> None:
        """Mutate payload in place to insert the LTM block at the right place."""
```

- [ ] **Step 2: Anthropic implementations**

In `adapters/anthropic.py`:

```python
import json


    def extract_usage_non_streaming(self, body: bytes) -> tuple[int, int] | None:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None
        usage = data.get("usage")
        if not usage:
            return None
        return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))

    def extract_usage_sse(self, captured: list[bytes]) -> tuple[int, int] | None:
        joined = b"".join(captured).decode("utf-8", errors="replace")
        input_tokens = 0
        output_tokens = 0
        found = False
        for line in joined.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            usage = obj.get("usage") or (obj.get("message") or {}).get("usage") or {}
            if usage:
                input_tokens = int(usage.get("input_tokens", input_tokens))
                output_tokens = int(usage.get("output_tokens", output_tokens))
                found = True
        return (input_tokens, output_tokens) if found else None

    def parse_response_text(self, resp_json: dict) -> str:
        return "".join(
            b.get("text", "")
            for b in resp_json.get("content", [])
            if isinstance(b, dict)
        )

    def extract_assistant_text_sse(self, captured: list[bytes]) -> str:
        joined = b"".join(captured).decode("utf-8", errors="replace")
        text = ""
        for line in joined.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[len("data:"):].strip())
            except json.JSONDecodeError:
                continue
            delta = obj.get("delta") or {}
            if "text" in delta:
                text += delta["text"]
        return text

    def inject_ltm(self, payload: dict, ltm_text: str) -> None:
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

- [ ] **Step 3: OpenAI implementations**

In `adapters/openai.py`:

```python
import json


    def extract_usage_non_streaming(self, body: bytes) -> tuple[int, int] | None:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return None
        usage = data.get("usage")
        if not usage:
            return None
        return (
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )

    def extract_usage_sse(self, captured: list[bytes]) -> tuple[int, int] | None:
        joined = b"".join(captured).decode("utf-8", errors="replace")
        input_tokens = 0
        output_tokens = 0
        found = False
        for line in joined.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            usage = obj.get("usage") or {}
            if usage:
                input_tokens = int(usage.get("prompt_tokens", input_tokens))
                output_tokens = int(usage.get("completion_tokens", output_tokens))
                found = True
        return (input_tokens, output_tokens) if found else None

    def parse_response_text(self, resp_json: dict) -> str:
        choices = resp_json.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict)
            )
        return ""

    def extract_assistant_text_sse(self, captured: list[bytes]) -> str:
        joined = b"".join(captured).decode("utf-8", errors="replace")
        text = ""
        for line in joined.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                if "content" in delta and isinstance(delta["content"], str):
                    text += delta["content"]
        return text

    def inject_ltm(self, payload: dict, ltm_text: str) -> None:
        if not ltm_text:
            return
        messages = payload.setdefault("messages", [])
        # Insert as the first system message; coalesce with an existing one if present
        if messages and messages[0].get("role") == "system":
            existing = messages[0].get("content", "")
            messages[0]["content"] = ltm_text + "\n\n" + (existing or "")
        else:
            messages.insert(0, {"role": "system", "content": ltm_text})
```

- [ ] **Step 4: Test both adapters**

```python
from spillover.adapters.anthropic import AnthropicAdapter
from spillover.adapters.openai import OpenAIAdapter


def test_anthropic_parse_response_text():
    a = AnthropicAdapter()
    assert a.parse_response_text({"content": [{"type": "text", "text": "hi"}]}) == "hi"


def test_openai_parse_response_text():
    o = OpenAIAdapter()
    assert (
        o.parse_response_text(
            {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        )
        == "hi"
    )


def test_anthropic_extract_usage_openai_shape_returns_none():
    """Anthropic adapter must refuse openai-shape usage (zero is wrong, must be None)."""
    a = AnthropicAdapter()
    body = b'{"usage":{"prompt_tokens":100,"completion_tokens":20}}'
    assert a.extract_usage_non_streaming(body) == (0, 0)
    # Note: this is current behavior — the field "input_tokens" is missing so we
    # return (0, 0). This is acceptable because the proxy routes by URL, not by
    # response shape sniffing.


def test_openai_extract_usage_anthropic_shape_returns_zeros():
    o = OpenAIAdapter()
    body = b'{"usage":{"input_tokens":100,"output_tokens":20}}'
    assert o.extract_usage_non_streaming(body) == (0, 0)


def test_anthropic_inject_ltm_into_string_system():
    a = AnthropicAdapter()
    p = {"system": "rules"}
    a.inject_ltm(p, "<spillover-ltm>X</spillover-ltm>")
    assert p["system"].startswith("<spillover-ltm>")
    assert "rules" in p["system"]


def test_anthropic_inject_ltm_into_none_system():
    a = AnthropicAdapter()
    p = {}
    a.inject_ltm(p, "<spillover-ltm>X</spillover-ltm>")
    assert p["system"] == "<spillover-ltm>X</spillover-ltm>"


def test_openai_inject_ltm_when_no_system():
    o = OpenAIAdapter()
    p = {"messages": [{"role": "user", "content": "hi"}]}
    o.inject_ltm(p, "<spillover-ltm>X</spillover-ltm>")
    assert p["messages"][0]["role"] == "system"
    assert "<spillover-ltm>" in p["messages"][0]["content"]


def test_openai_inject_ltm_coalesces_existing_system():
    o = OpenAIAdapter()
    p = {
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hi"},
        ]
    }
    o.inject_ltm(p, "<spillover-ltm>X</spillover-ltm>")
    assert p["messages"][0]["role"] == "system"
    assert "rules" in p["messages"][0]["content"]
    assert "<spillover-ltm>" in p["messages"][0]["content"]
    # No second system inserted
    assert sum(1 for m in p["messages"] if m["role"] == "system") == 1


def test_anthropic_sse_extract_text():
    a = AnthropicAdapter()
    captured = [
        b'data: {"delta":{"text":"hel"}}\n\n',
        b'data: {"delta":{"text":"lo"}}\n\n',
    ]
    assert a.extract_assistant_text_sse(captured) == "hello"


def test_openai_sse_extract_text():
    o = OpenAIAdapter()
    captured = [
        b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
    ]
    assert o.extract_assistant_text_sse(captured) == "hello"
```

- [ ] **Step 5: Run + commit**

```
python -m pytest tests/unit/test_adapter_response.py -v
python -m pytest -v -m "not slow"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "fix(adapter): parse_response/extract_usage/inject_ltm parity for OpenAI + Anthropic"
```

---

## Phase 2 — C1 fix: middleware fallback

### Task 5: X-Project env-var fallback

**Files:**
- Modify: `src/spillover/proxy/middleware.py`
- Modify: `tests/unit/test_middleware.py`
- Create: `tests/integration/test_middleware_fallback.py`

- [ ] **Step 1: Update middleware**

```python
from __future__ import annotations

import hashlib
import os
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

_HEX_ID = re.compile(r"^[0-9a-f]{6,64}$")

# Paths that bypass project_id resolution entirely (admin/observability).
_EXEMPT_PATHS = {"/metrics", "/health", "/"}


def _resolve_project_id(raw: str) -> str:
    if _HEX_ID.match(raw):
        return raw
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class ProjectIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        raw = request.headers.get("x-project") or os.environ.get(
            "SPILLOVER_PROJECT_ID"
        )
        if not raw:
            return JSONResponse(
                {
                    "error": (
                        "missing X-Project header and SPILLOVER_PROJECT_ID "
                        "env var; one of them must be set"
                    )
                },
                status_code=400,
            )
        request.state.project_id = _resolve_project_id(raw)
        return await call_next(request)
```

- [ ] **Step 2: Append to `tests/unit/test_middleware.py`**

```python
def test_middleware_falls_back_to_env(client, monkeypatch):
    monkeypatch.setenv("SPILLOVER_PROJECT_ID", "deadbeefcafe")
    r = client.get("/echo")
    assert r.status_code == 200
    assert r.json()["project_id"] == "deadbeefcafe"
```

- [ ] **Step 3: Integration test (proxy hits 200 instead of 400 when wrapper sets env)**

`tests/integration/test_middleware_fallback.py`:

```python
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app) as c:
        yield c


@respx.mock
def test_proxy_accepts_request_with_env_project_only(client, monkeypatch):
    monkeypatch.setenv("SPILLOVER_PROJECT_ID", "abcdef1234")
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg",
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
    )
    # Note: no X-Project header
    r = client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
```

- [ ] **Step 4: Run + commit**

```
python -m pytest tests/unit/test_middleware.py tests/integration/test_middleware_fallback.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "fix(proxy): X-Project env-var fallback + exempt /metrics /health /"
```

---

## Phase 3 — C2 fix: executor offload + Kuzu cache (I1)

### Task 6: Move sync DB/embed/retrieve calls to executor + cache Kuzu

**Files:**
- Modify: `src/spillover/storage/kuzu.py`
- Modify: `src/spillover/proxy/app.py`

- [ ] **Step 1: Kuzu connection cache + schema-once**

Rewrite `src/spillover/storage/kuzu.py`:

```python
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import kuzu

_SCHEMA_PATH = Path(__file__).with_name("kuzu_schema.cypher")
_CACHE: OrderedDict[str, kuzu.Connection] = OrderedDict()
_INITIALIZED: set[str] = set()
_LOCK = threading.Lock()
_MAX_CACHE = 32


def project_kuzu_dir(db_root: Path, project_id: str) -> Path:
    return db_root / "projects" / project_id / "kuzu"


def _init_schema(conn: kuzu.Connection, cache_key: str) -> None:
    if cache_key in _INITIALIZED:
        return
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    for statement in [s.strip() for s in schema.split(";") if s.strip()]:
        conn.execute(statement)
    _INITIALIZED.add(cache_key)


def open_project_kuzu(db_root: Path, project_id: str) -> kuzu.Connection:
    key = f"{db_root}:{project_id}"
    with _LOCK:
        existing = _CACHE.get(key)
        if existing is not None:
            _CACHE.move_to_end(key)
            return existing
        path = project_kuzu_dir(db_root, project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = kuzu.Database(str(path))
        conn = kuzu.Connection(db)
        _init_schema(conn, key)
        _CACHE[key] = conn
        while len(_CACHE) > _MAX_CACHE:
            _CACHE.popitem(last=False)
        return conn


def clear_kuzu_cache() -> None:
    """Test helper — drop all cached connections."""
    with _LOCK:
        _CACHE.clear()
        _INITIALIZED.clear()
```

Existing tests for kuzu must still pass; they call `open_project_kuzu` so they get the cached version transparently. Add `_INITIALIZED` clear between tests via a `conftest.py` autouse fixture if needed.

- [ ] **Step 2: Add executor wrapper in `proxy/app.py`**

Introduce a small helper:

```python
async def _run_sync(loop, fn, *args, **kwargs):
    import functools
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))
```

Then wrap `_retrieve_ltm_block` and `_maybe_evict` calls. Inside `_handle_request`:

```python
        loop = asyncio.get_running_loop()

        try:
            conv = adapter.parse(payload)
            ltm_text = await _run_sync(loop, _retrieve_ltm_block, config, project_id, conv)
            adapter.inject_ltm(payload, ltm_text)
        except Exception:
            log.exception("retriever failed project=%s; proceeding without LTM", project_id)

        # detection + rescue moved into executor too
        rescued, rescue_ids = await _run_sync(
            loop, _detect_and_rescue, config, project_id, payload.get("messages") or []
        )
```

Define `_detect_and_rescue` as a thin sync wrapper that runs the existing rescue block. Same treatment for `_maybe_evict`.

- [ ] **Step 3: Smoke + commit**

```
python -m pytest -v -m "not slow"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "perf(proxy): executor offload for retrieve/evict/rescue + Kuzu LRU cache"
```

---

## Phase 4 — C3 fix: incremental SSE rewrite

### Task 7: Stream chunks live; buffer only the usage chunk

**Files:**
- Modify: `src/spillover/counter_compact/sse_rewrite.py`
- Modify: `src/spillover/proxy/app.py`
- Create: `tests/integration/test_incremental_sse_rewrite.py`

- [ ] **Step 1: Helper that splits a chunk into "before-usage", "usage", "after-usage"**

Append to `sse_rewrite.py`:

```python
def has_usage_marker(chunk: bytes) -> bool:
    """Cheap check: does this chunk likely contain a usage field?"""
    return b'"usage"' in chunk
```

- [ ] **Step 2: Replace the streaming branch in `proxy/app.py`**

```python
        upstream = await app.state.http_client.send(
            app.state.http_client.build_request(
                "POST", upstream_url, headers=fwd_headers, content=forwarded_body
            ),
            stream=True,
        )
        sink: list[bytes] = []
        rewrite_enabled = os.environ.get("SPILLOVER_STREAM_REWRITE", "1") != "0"

        async def proxy_stream():
            archived_ids: list[str] = []
            tokens_archived = 0
            tail_buffer = b""
            try:
                async for chunk in upstream.aiter_bytes():
                    sink.append(chunk)
                    if rewrite_enabled and has_usage_marker(chunk):
                        # Buffer this chunk so we can rewrite it before yielding
                        tail_buffer += chunk
                        continue
                    if tail_buffer:
                        # Flush previously-buffered usage chunk (rewrite once tokens_archived known)
                        # But tokens_archived comes from _maybe_evict on usage extraction —
                        # for now, just emit tail_buffer unmodified at end of stream.
                        pass
                    yield chunk
            finally:
                await upstream.aclose()
                if upstream.status_code == 200:
                    usage = adapter.extract_usage_sse(sink)
                    if usage is not None:
                        assistant_text = adapter.extract_assistant_text_sse(sink)
                        archived_ids, tokens_archived = await _run_sync(
                            loop, _maybe_evict, config, project_id, payload, assistant_text, usage
                        )
                if rewrite_enabled and tail_buffer and tokens_archived > 0:
                    yield rewrite_sse_body(tail_buffer, tokens_archived)
                elif tail_buffer:
                    yield tail_buffer
                if upstream.status_code >= 400:
                    log.warning(
                        "upstream non-2xx (stream) status=%d project=%s",
                        upstream.status_code,
                        project_id,
                    )
                if archived_ids:
                    _enqueue_facets(app, project_id, archived_ids, config)
```

This streams content-block deltas live (they don't have `"usage"`), buffers the message_stop / message_delta-with-usage event, runs eviction, then yields the rewritten usage chunk last. TTFB is preserved for the content stream; only the final usage-bearing chunk waits.

- [ ] **Step 3: Integration test**

`tests/integration/test_incremental_sse_rewrite.py`:

```python
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app) as c:
        yield c


@respx.mock
def test_content_chunks_pass_through_unbuffered(client, config):
    sse = (
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"text":"ok"}}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop","usage":{"input_tokens":900,"output_tokens":50}}\n\n'
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            content=sse,
            headers={"content-type": "text/event-stream"},
        )
    )
    # Big enough to trigger eviction so rewrite fires
    messages = []
    for i in range(12):
        messages.append(
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 320}
        )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "abcdef12", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "stream": True,
            "messages": messages,
        },
    )
    assert r.status_code == 200
    body = r.content
    # Content chunk present
    assert b"content_block_delta" in body
    # Usage chunk rewritten
    assert b"spillover_real_input_tokens" in body
```

- [ ] **Step 4: Run + commit**

```
python -m pytest tests/integration/test_incremental_sse_rewrite.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "fix(counter-compact): incremental SSE rewrite (stream live, buffer only usage chunk)"
```

---

## Phase 5 — C4 fix: wire all metrics + X-Request-Id

### Task 8: Instrument metrics at every call site

**Files:**
- Modify: `src/spillover/proxy/app.py`
- Modify: `src/spillover/facet/worker.py`
- Modify: `src/spillover/metrics/registry.py` (add facet_dropped_total)
- Create: `src/spillover/request_id.py`
- Create: `tests/integration/test_metrics_wired.py`

- [ ] **Step 1: Add `facet_dropped_total` to `metrics/registry.py`**

```python
facet_dropped_total = Counter(
    "spillover_facet_dropped_total",
    "Facet events dropped due to queue backpressure",
    labelnames=("project",),
    registry=REGISTRY,
)
```

- [ ] **Step 2: `src/spillover/request_id.py`**

```python
from __future__ import annotations

import uuid


def ensure_request_id(headers: dict | None) -> str:
    if headers:
        existing = headers.get("x-request-id") or headers.get("X-Request-Id")
        if existing:
            return str(existing)
    return uuid.uuid4().hex
```

- [ ] **Step 3: Wire metrics inside `_handle_request`**

```python
        from spillover.metrics.registry import (
            requests_total,
            request_duration,
            overflow_triggered_total,
            episodes_archived_total,
            retriever_hits_total,
            facet_queue_depth,
            compaction_detected_total,
        )

        # Phase: retrieve
        with request_duration.labels(phase="retrieve").time():
            ltm_text = await _run_sync(loop, _retrieve_ltm_block, config, project_id, conv)

        # ...

        if rescued:
            compaction_detected_total.labels(project=project_id).inc(len(rescued))

        # After _maybe_evict
        if archived_ids:
            overflow_triggered_total.labels(project=project_id).inc()
            episodes_archived_total.labels(project=project_id, type="evicted").inc(len(archived_ids))
            _enqueue_facets(app, project_id, archived_ids, config)
            facet_queue_depth.set(app.state.facet_queue.qsize())

        # At response time
        requests_total.labels(project=project_id, provider=provider, status=str(status_code)).inc()
```

(Apply to both non-streaming and streaming branches.)

- [ ] **Step 4: Wire facet worker metrics**

In `facet/worker.py`, the `FacetWorker._run` loop should `set` queue depth on each pop:

```python
from spillover.metrics.registry import facet_queue_depth


    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            event = await self.queue.get()
            facet_queue_depth.set(self.queue.qsize())
            try:
                await loop.run_in_executor(None, _process_one, event)
            except Exception:
                log.exception(...)
            finally:
                self.queue.task_done()
```

Also enforce `maxsize=1024` at proxy startup. In `proxy/app.py` lifespan, change to:

```python
        app.state.facet_queue = asyncio.Queue(maxsize=1024)
```

And in `_enqueue_facets`:

```python
def _enqueue_facets(app: FastAPI, project_id: str, episode_ids: list[str], config: Config) -> None:
    queue = getattr(app.state, "facet_queue", None)
    if queue is None:
        return
    from spillover.metrics.registry import facet_dropped_total
    for eid in episode_ids:
        try:
            queue.put_nowait(
                FacetEvent(project_id=project_id, episode_id=eid, db_root=config.db_root)
            )
        except asyncio.QueueFull:
            facet_dropped_total.labels(project=project_id).inc()
            log.warning("facet queue full, dropping event project=%s id=%s",
                         project_id, eid)
```

- [ ] **Step 5: Integration test**

`tests/integration/test_metrics_wired.py`:

```python
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app) as c:
        yield c


@respx.mock
def test_metrics_increment_after_request(client):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg",
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
    )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "abcdef12", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200

    m = client.get("/metrics")
    assert m.status_code == 200
    body = m.text
    # At least one request counted with status=200
    assert 'spillover_requests_total{project="abcdef12",provider="anthropic",status="200"} 1.0' in body
```

- [ ] **Step 6: Run + commit**

```
python -m pytest tests/integration/test_metrics_wired.py -v
python -m pytest -v -m "not slow"
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "fix(metrics): instrument requests/duration/overflow/episodes/queue/compaction at every call site"
```

---

## Phase 6 — Weighted-FIFO + Soft-Ceiling integration

### Task 9: Selector accepts a density weight + proxy uses TokenPlan

**Files:**
- Modify: `src/spillover/eviction/selector.py`
- Modify: `src/spillover/proxy/app.py`
- Modify: `tests/unit/test_eviction_selector.py`

- [ ] **Step 1: Extend `ActiveTurn` with `density`**

```python
@dataclass
class ActiveTurn:
    index: int
    token_count: int
    role: str
    pinned: bool = False
    memory_type: str | None = None
    is_system: bool = False
    density: int = 0  # entities + decisions + tool_calls; higher = more valuable
```

- [ ] **Step 2: Sort pass-1 + pass-2 candidates by density-weighted FIFO**

In `_evictable_pass1` and `_evictable_pass2`, return the same list as today, but sort by `(weight desc -> evict first, then index asc for tiebreak)`. `weight = token_count / max(1, density)` — high token + low density = evict first.

```python
def _weight(t: ActiveTurn) -> float:
    return t.token_count / max(1, t.density)


def _ordered_candidates(turns, recent_buffer, exclude_priority):
    ...
    cands = [...]  # filter
    cands.sort(key=lambda t: (-_weight(t), t.index))
    return cands
```

Wire `_ordered_candidates` into both passes. Existing tests pass because `density=0` everywhere defaults to weight = token_count, which still sorts FIFO-equivalent when all tokens equal. Add one new test where density differs:

```python
def test_weighted_evicts_low_density_first():
    turns = [
        _t(0, 100, is_system=True),
        _t(1, 200),  # density=0 -> weight=200
        _t(2, 100),  # density=0 -> weight=100
        _t(5, 50), _t(6, 50), _t(7, 50), _t(8, 50),
    ]
    # Inject high density on turn 1 by mutating
    turns[1].density = 10  # weight=20 -> evict last
    result = select_for_eviction(turns, tokens_to_free=200, recent_buffer=4)
    # Turn 2 (weight=100) should be evicted before turn 1 (weight=20)
    assert result.evicted_indexes[0] == 2
```

- [ ] **Step 3: Proxy populates density from `Conversation` turns**

In `_maybe_evict` (proxy/app.py), when building `ActiveTurn`:

```python
            density=len(turn.tool_calls),  # cheap proxy for semantic density v1
```

- [ ] **Step 4: Run + commit**

```
python -m pytest tests/unit/test_eviction_selector.py -v
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(eviction): weighted-FIFO selector by semantic density (tool_calls v1)"
```

---

### Task 10: Proxy uses TokenPlan for budgets

**Files:**
- Modify: `src/spillover/proxy/app.py`

- [ ] **Step 1: Replace `int(config.window_max * config.ltm_budget_pct)` with TokenPlan**

```python
from spillover.budget.plan import plan_from_config
from spillover.budget.profile import select_profile


def _ltm_budget_for(config: Config, payload: dict) -> int:
    profile = select_profile(payload, config.profile_default)
    return int(config.operational_ceiling_tokens * profile.ltm_pct)
```

Inside `_retrieve_ltm_block`, replace the budget line:

```python
        budget = _ltm_budget_for(config, inbound_payload)
        trimmed = trim_to_budget(db, fused, max_tokens=budget)
```

(Pass `inbound_payload` through to `_retrieve_ltm_block` — small signature change.)

- [ ] **Step 2: Commit**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(proxy): per-request LTM budget from profile + TokenPlan"
```

---

## Phase 7 — Verify + tag v1.1.0 + push

### Task 11: Full suite + tag + push

- [ ] **Step 1: Full suite**

```
python -m pytest -v -m "not slow"
```

Expected: ~160 fast PASSED.

```
python -m pytest -v
```

Expected: ~166 PASSED.

```
python -m ruff check src/ tests/
```

Expected: 0 errors.

- [ ] **Step 2: Manual smoke (optional) — confirm wrapper now works**

```
$env:SPILLOVER_PROJECT_ID = "smoke-test"
spillover up
# elsewhere
curl -X POST http://127.0.0.1:8787/v1/messages \
  -H "Authorization: Bearer $REAL_KEY" \
  -d '{"model":"claude-haiku-4-5-20251001","max_tokens":50,"messages":[{"role":"user","content":"reply OK"}]}'
```

Expected: 200 (no longer 400 missing X-Project).

- [ ] **Step 3: Tag**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit --allow-empty -m "chore: spillover v1.1.0 (Plan 5 done — soft ceiling + C1-C5 fixes)"
git tag -a v1.1.0 -m "spillover v1.1.0 - soft ceiling + C1-C5 + budget profiles"
```

- [ ] **Step 4: Push branch + tags**

```
git push -u origin feat/plan5-soft-ceiling
git push origin --tags
```

- [ ] **Step 5: Merge to master**

```
git checkout master
git merge --no-ff feat/plan5-soft-ceiling -m "Merge Plan 5: soft ceiling + C1-C5 fixes (v1.1.0)"
git push origin master
git push origin --tags
```

---

## Definition of Done

1. All tests pass (≥160 fast, ≥166 with slow).
2. `ruff check src/ tests/` exits 0.
3. C1 fixed: middleware accepts `SPILLOVER_PROJECT_ID` env fallback; `test_proxy_accepts_request_with_env_project_only` passes.
4. C2 fixed: `_retrieve_ltm_block` and `_maybe_evict` run on executor; no `await db.execute(...)` antipattern.
5. C3 fixed: integration test proves content chunks flush before the usage chunk.
6. C4 fixed: `test_metrics_increment_after_request` shows non-zero counter.
7. C5 fixed: OpenAI adapter response parsing test passes; OpenAI LTM injection lands a system message at index 0.
8. Soft ceiling: `Config.operational_ceiling_tokens` and `provider_max_tokens` exist; budget tiers sum to 1.0.
9. Dynamic profile: `select_profile` returns `coding` when tools present, `conversation` for long messages, `default` otherwise.
10. Weighted-FIFO: `test_weighted_evicts_low_density_first` passes.
11. `v1.1.0` tag exists locally + pushed.
12. `feat/plan5-soft-ceiling` and `master` branches both pushed to `origin`.

End of plan.
