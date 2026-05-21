# spillover Multi-CLI + Polish Implementation Plan (Plan 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out v1.0 by adding the OpenAI adapter, decay scheduler, Prometheus metrics, additional CLI wrappers, streaming SSE usage rewrite (Plan 3 deferred this), and an A/B benchmark harness. After this plan, spillover is a complete v1 product.

**Architecture:** OpenAI adapter is plug-and-play behind the existing `Adapter` ABC. Decay scheduler is a background `asyncio` task started in the proxy's lifespan that walks `vec_episodes` every 6 hours and applies `importance = base * exp(-age/half_life) + min(hit_count*0.05, 0.5)`. Prometheus metrics live behind a new `GET /metrics` route on the proxy. Streaming SSE usage rewrite splits the captured chunks to find the `message_delta` or `message_stop` event, replaces its `usage` field, and re-emits the modified SSE stream. Additional wrappers (`spillover-codex`, `spillover-cursor`, `spillover-continue`) follow the same shape as `spillover-cc` from Plan 3. The A/B benchmark is a standalone script that runs N coding tasks against vanilla vs spillover-wired Claude Code and produces a markdown report.

**Tech Stack additions:**
- `prometheus-client>=0.20` — metrics exposition

Everything else uses existing Plan 1–3 modules.

End state of this plan:
- OpenAI `/v1/chat/completions` requests are accepted by the proxy.
- Streaming responses also rewrite `usage.input_tokens` on the way back to the client.
- `GET /metrics` exposes Prometheus metrics.
- Decay scheduler shrinks importance over time; pinned episodes are exempt.
- 4 wrapper scripts ship: `spillover-cc` (already there), `spillover-codex`, `spillover-cursor`, `spillover-continue`.
- `python -m spillover.bench.ab` runs the A/B harness and writes a markdown report.
- All Plan 1–3 tests still pass; ~30 new tests cover the new code.
- `v1.0.0` tagged after this plan.

---

## File structure

New files:

```
src/spillover/
  adapters/openai.py                # OpenAI Chat Completions adapter
  decay/__init__.py
  decay/scheduler.py                # Background task
  metrics/__init__.py
  metrics/registry.py               # Prometheus metric definitions
  metrics/middleware.py             # Increment counters in the proxy
  counter_compact/sse_rewrite.py    # SSE usage rewrite for streaming
  wrappers/codex.py
  wrappers/cursor.py
  wrappers/continue_dev.py          # named for Continue.dev
  bench/__init__.py
  bench/ab.py                       # standalone benchmark runner
tests/unit/
  test_adapter_openai.py
  test_decay_scheduler.py
  test_metrics_registry.py
  test_sse_rewrite.py
  test_wrappers_extra.py
tests/integration/
  test_openai_passthrough.py
  test_streaming_usage_rewrite.py
  test_metrics_endpoint.py
```

Modified files:

```
src/spillover/proxy/app.py          # add /metrics route, decay scheduler, OpenAI route, SSE usage rewrite
src/spillover/cli.py                # `spillover bench` subcommand
pyproject.toml                      # prometheus-client dep, new entry points
```

---

## Phase 0 — OpenAI adapter

### Task 1: OpenAI adapter

**Files:**
- Create: `src/spillover/adapters/openai.py`
- Create: `tests/unit/test_adapter_openai.py`

OpenAI's Chat Completions wire format differs from Anthropic's in 3 ways:
1. `system` is a `{"role": "system", ...}` message inside `messages`, not a top-level field.
2. Content is usually a plain string; multi-modal uses `[{"type":"text"|"image_url", ...}]`.
3. Tool calls are reported as `assistant.message.tool_calls = [{...}]`, not as `tool_use` content blocks.

The adapter normalizes inbound OpenAI requests into the same `Conversation` dataclass; on `build()`, it emits back the OpenAI shape.

- [ ] **Step 1: Write `openai.py`**

```python
from __future__ import annotations

from typing import Any

from spillover.adapters.base import Adapter, Conversation, ConversationTurn
from spillover.eviction.tokenizer import count_tokens

_PASSTHROUGH_KEYS = {
    "stream",
    "stop",
    "temperature",
    "top_p",
    "n",
    "logprobs",
    "top_logprobs",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "tools",
    "tool_choice",
    "response_format",
    "user",
}


class OpenAIAdapter(Adapter):
    def parse(self, payload: dict) -> Conversation:
        system_parts: list[str] = []
        turns: list[ConversationTurn] = []

        for i, msg in enumerate(payload.get("messages", [])):
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                continue
            tool_calls = self._extract_tool_calls(msg)
            tok = count_tokens(content)
            turns.append(
                ConversationTurn(
                    role=role,
                    content=content,
                    tool_calls=tool_calls,
                    token_count=tok,
                    source_index=i,
                )
            )

        system = "\n\n".join(system_parts) if system_parts else None
        system_tokens = count_tokens(system) if system else 0
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
        messages: list[dict] = []
        if conversation.system:
            sys = conversation.system if isinstance(conversation.system, str) else (
                "\n\n".join(
                    b.get("text", "") for b in conversation.system
                    if isinstance(b, dict)
                )
            )
            messages.append({"role": "system", "content": sys})
        for t in conversation.turns:
            entry = {"role": t.role, "content": t.content}
            if t.tool_calls:
                entry["tool_calls"] = t.tool_calls
            messages.append(entry)
        payload: dict = {
            "model": conversation.model,
            "max_tokens": conversation.max_tokens,
            "messages": messages,
        }
        payload.update(conversation.extra)
        return payload

    def _extract_tool_calls(self, msg: dict) -> list[dict]:
        return list(msg.get("tool_calls") or [])
```

- [ ] **Step 2: Test**

```python
from spillover.adapters.openai import OpenAIAdapter
from spillover.adapters.base import Conversation, ConversationTurn


def test_parse_basic():
    payload = {
        "model": "gpt-4o",
        "max_tokens": 100,
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    }
    conv = OpenAIAdapter().parse(payload)
    assert conv.model == "gpt-4o"
    assert conv.max_tokens == 100
    assert conv.system == "be brief"
    assert len(conv.turns) == 2
    assert conv.turns[0].role == "user"


def test_parse_multiple_system_messages_concatenated():
    payload = {
        "model": "gpt-4o",
        "max_tokens": 100,
        "messages": [
            {"role": "system", "content": "rule 1"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "rule 2"},
        ],
    }
    conv = OpenAIAdapter().parse(payload)
    assert conv.system == "rule 1\n\nrule 2"


def test_parse_extra_preserved():
    payload = {
        "model": "gpt-4o",
        "max_tokens": 100,
        "stream": True,
        "temperature": 0.7,
        "messages": [{"role": "user", "content": "hi"}],
    }
    conv = OpenAIAdapter().parse(payload)
    assert conv.extra.get("stream") is True
    assert conv.extra.get("temperature") == 0.7


def test_parse_tool_calls():
    payload = {
        "model": "gpt-4o",
        "max_tokens": 100,
        "messages": [
            {
                "role": "assistant",
                "content": "calling tool",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "search", "arguments": "{}"},
                    }
                ],
            }
        ],
    }
    conv = OpenAIAdapter().parse(payload)
    assert len(conv.turns[0].tool_calls) == 1
    assert conv.turns[0].tool_calls[0]["id"] == "tc1"


def test_build_roundtrip():
    payload = {
        "model": "gpt-4o",
        "max_tokens": 100,
        "stream": True,
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ],
    }
    adapter = OpenAIAdapter()
    rebuilt = adapter.build(adapter.parse(payload))
    assert rebuilt["model"] == "gpt-4o"
    assert rebuilt["max_tokens"] == 100
    assert rebuilt["stream"] is True
    assert {"role": "system", "content": "be brief"} in rebuilt["messages"]
    assert {"role": "user", "content": "hi"} in rebuilt["messages"]


def test_build_omits_system_when_none():
    conv = Conversation(
        system=None,
        turns=[ConversationTurn(role="user", content="hi", tool_calls=[], token_count=1)],
        model="gpt-4o",
        max_tokens=100,
    )
    rebuilt = OpenAIAdapter().build(conv)
    assert all(m["role"] != "system" for m in rebuilt["messages"])
```

- [ ] **Step 3: Run + commit**

```
python -m pytest tests/unit/test_adapter_openai.py -v
```

Expected: 6 PASSED.

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(adapter/openai): OpenAI Chat Completions adapter"
```

---

## Phase 1 — OpenAI passthrough route

### Task 2: Add `/v1/chat/completions` route to the proxy

**Files:**
- Modify: `src/spillover/proxy/app.py`
- Create: `tests/integration/test_openai_passthrough.py`

The OpenAI route mirrors the existing `/v1/messages` route but uses `OpenAIAdapter`. The same retrieval, eviction, counter-compaction logic applies; the only differences are:
1. The adapter is `OpenAIAdapter()` instead of `AnthropicAdapter()`.
2. The forward URL is `{config.openai_base_url}/v1/chat/completions` — add a new `openai_base_url` field to `Config` with default `https://api.openai.com`.
3. Usage shape differs: OpenAI uses `prompt_tokens`/`completion_tokens` instead of `input_tokens`/`output_tokens`. Map both in the usage helpers.

Implementation outline:
- Add `openai_base_url: str` to `Config`, default from env var `SPILLOVER_OPENAI_BASE_URL`.
- Refactor `_extract_usage_non_streaming` to accept a mapping `{"input": "prompt_tokens"|"input_tokens", ...}` or split into two helpers: `_extract_usage_anthropic` and `_extract_usage_openai`.
- Refactor `_maybe_evict` to accept the adapter so it parses with the right shape.
- Add the new POST `/v1/chat/completions` handler; it calls a shared inner function with the right adapter.

Test the passthrough first, defer eviction wiring to ensure forward + non-stream + stream work. Then a separate small test verifies eviction triggers on the OpenAI path too.

- [ ] **Step 1: Implement Config addition**

In `src/spillover/config.py`, add field `openai_base_url: str` after `upstream_base_url`. Defaults: `os.environ.get("SPILLOVER_OPENAI_BASE_URL", "https://api.openai.com")`.

Update `tests/unit/test_config.py` to assert the new default.

- [ ] **Step 2: Refactor proxy**

Extract the body of the existing `messages` handler into a private helper `async def _handle_request(request, adapter, upstream_url)`. Then have two route functions:

```python
    @app.post("/v1/messages")
    async def messages_anthropic(request: Request):
        return await _handle_request(
            request,
            adapter=AnthropicAdapter(),
            upstream_url=f"{config.upstream_base_url}/v1/messages",
            provider="anthropic",
        )

    @app.post("/v1/chat/completions")
    async def messages_openai(request: Request):
        return await _handle_request(
            request,
            adapter=OpenAIAdapter(),
            upstream_url=f"{config.openai_base_url}/v1/chat/completions",
            provider="openai",
        )
```

Inside `_handle_request`, branch on `provider` for usage extraction (OpenAI uses `prompt_tokens` / `completion_tokens`). Keep all the existing intercept / retrieval / eviction / facet-enqueue logic; nothing else changes.

- [ ] **Step 3: Test passthrough**

`tests/integration/test_openai_passthrough.py`:

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
def test_openai_passthrough_non_streaming(client):
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 1,
                    "total_tokens": 6,
                },
            },
        )
    )
    r = client.post(
        "/v1/chat/completions",
        headers={"X-Project": "abcdef12", "Authorization": "Bearer sk-test"},
        json={
            "model": "gpt-4o",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "hi"


@respx.mock
def test_openai_passthrough_4xx(client):
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    r = client.post(
        "/v1/chat/completions",
        headers={"X-Project": "abcdef12", "Authorization": "Bearer bad"},
        json={
            "model": "gpt-4o",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 401
```

- [ ] **Step 4: Run + commit**

```
python -m pytest tests/integration/test_openai_passthrough.py -v
```

Expected: 2 PASSED.

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(proxy): /v1/chat/completions route with OpenAI adapter"
```

---

## Phase 2 — Streaming SSE usage rewrite

### Task 3: Rewrite streaming usage too

**Files:**
- Create: `src/spillover/counter_compact/sse_rewrite.py`
- Create: `tests/unit/test_sse_rewrite.py`
- Modify: `src/spillover/proxy/app.py`
- Create: `tests/integration/test_streaming_usage_rewrite.py`

Anthropic's streaming format ends with a `message_stop` (or `message_delta`) event whose `data:` contains a usage dict. We need to:
1. Capture chunks as before.
2. Once the upstream stream completes, walk the captured bytes, find any `data:` line with `usage`, rewrite it, and re-emit the modified stream to the client.

The trick: the body is already streamed to the client during the proxy_stream loop. To rewrite, we must NOT stream chunks live — we buffer first, rewrite, then yield. This trades latency-to-first-byte for usage integrity. For client-side compaction defense, this is the correct tradeoff (the alternative is letting the real `input_tokens` reach the client and trigger /compact).

Add an opt-out env var `SPILLOVER_STREAM_REWRITE=0` to fall back to the live-stream behavior from Plan 3 (no rewrite, faster TTFB).

- [ ] **Step 1: Write `sse_rewrite.py`**

```python
from __future__ import annotations

import json
import re

from spillover.counter_compact.usage_rewrite import rewrite_usage

_DATA_LINE = re.compile(rb"^data:\s*(.*)$", re.MULTILINE)


def rewrite_sse_body(body: bytes, tokens_archived_this_turn: int) -> bytes:
    """Walk SSE chunks, rewrite any data: line containing 'usage'."""
    if tokens_archived_this_turn <= 0 or not body:
        return body

    def _rewrite_match(m: re.Match) -> bytes:
        raw = m.group(1).strip()
        if not raw or raw == b"[DONE]":
            return m.group(0)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return m.group(0)
        usage_path = None
        if "usage" in obj:
            usage_path = obj
        elif isinstance(obj.get("message"), dict) and "usage" in obj["message"]:
            usage_path = obj["message"]
        if usage_path is None:
            return m.group(0)
        usage_path["usage"] = rewrite_usage(
            usage_path["usage"], tokens_archived_this_turn
        )
        rebuilt = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        return b"data: " + rebuilt

    return _DATA_LINE.sub(_rewrite_match, body)
```

- [ ] **Step 2: Tests**

`tests/unit/test_sse_rewrite.py`:

```python
import json

from spillover.counter_compact.sse_rewrite import rewrite_sse_body


def test_rewrite_message_stop_usage():
    body = (
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop","usage":{"input_tokens":1000,"output_tokens":50}}\n\n'
    )
    out = rewrite_sse_body(body, 400)
    assert b"input_tokens" in out
    # Extract and check
    for line in out.splitlines():
        if line.startswith(b"data: ") and b"usage" in line:
            obj = json.loads(line[len(b"data: "):])
            assert obj["usage"]["input_tokens"] == 600
            assert obj["usage"]["spillover_real_input_tokens"] == 1000
            break
    else:
        raise AssertionError("usage line not found")


def test_rewrite_message_delta_nested_usage():
    body = (
        b'event: message_delta\ndata: {"type":"message_delta","message":{"usage":{"input_tokens":800}}}\n\n'
    )
    out = rewrite_sse_body(body, 300)
    obj = None
    for line in out.splitlines():
        if line.startswith(b"data:") and b"usage" in line:
            obj = json.loads(line[len(b"data: "):])
            break
    assert obj is not None
    assert obj["message"]["usage"]["input_tokens"] == 500


def test_rewrite_no_op_when_archived_zero():
    body = b'data: {"usage":{"input_tokens":100}}\n\n'
    assert rewrite_sse_body(body, 0) == body


def test_rewrite_no_op_when_no_usage():
    body = b'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n'
    assert rewrite_sse_body(body, 50) == body
```

- [ ] **Step 3: Wire into proxy streaming branch**

Currently the streaming branch yields chunks live. Replace the live-stream behavior with a buffered approach when usage rewrite is needed. Pseudo:

```python
        if not _stream_rewrite_enabled(config):
            # Plan 3 behavior — yield live, no rewrite
            async def proxy_stream():
                ...
                # existing code
        else:
            # Buffer fully, rewrite usage, then yield
            buf = b""
            async with app.state.http_client.stream(...) as upstream:
                async for chunk in upstream.aiter_bytes():
                    buf += chunk
            usage = _extract_usage_sse([buf])
            archived_ids, tokens_archived = _maybe_evict(...) if usage else ([], 0)
            if tokens_archived > 0:
                buf = rewrite_sse_body(buf, tokens_archived)
            # then yield buf as a single chunk
            async def proxy_stream():
                yield buf
```

Add a helper `_stream_rewrite_enabled(config) -> bool` reading the env var with default `True`.

- [ ] **Step 4: Integration test**

`tests/integration/test_streaming_usage_rewrite.py`:

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
def test_streaming_usage_rewrite_applied(client, config):
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
    pid = "abcdef12"
    messages = []
    for i in range(12):
        messages.append({"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 320})
    r = client.post(
        "/v1/messages",
        headers={"X-Project": pid, "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "stream": True,
            "messages": messages,
        },
    )
    assert r.status_code == 200
    assert b"spillover_real_input_tokens" in r.content


@respx.mock
def test_streaming_rewrite_disabled_via_env(client, monkeypatch):
    monkeypatch.setenv("SPILLOVER_STREAM_REWRITE", "0")
    # Force a fresh app instance picking up the new env
    sse = (
        b'event: message_stop\ndata: {"type":"message_stop","usage":{"input_tokens":900,"output_tokens":50}}\n\n'
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}),
    )
    r = client.post(
        "/v1/messages",
        headers={"X-Project": "abcdef12", "Authorization": "Bearer t"},
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200
    # No rewrite -> real_input_tokens absent
    assert b"spillover_real_input_tokens" not in r.content
```

- [ ] **Step 5: Run + commit**

```
python -m pytest tests/unit/test_sse_rewrite.py tests/integration/test_streaming_usage_rewrite.py -v
```

Expected: 6 PASSED.

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(counter-compact): streaming SSE usage rewrite (buffered)"
```

---

## Phase 3 — Prometheus metrics

### Task 4: /metrics endpoint

**Files:**
- Modify: `pyproject.toml` (add `prometheus-client>=0.20`)
- Create: `src/spillover/metrics/__init__.py` (empty)
- Create: `src/spillover/metrics/registry.py`
- Create: `tests/unit/test_metrics_registry.py`
- Create: `tests/integration/test_metrics_endpoint.py`
- Modify: `src/spillover/proxy/app.py`

- [ ] **Step 1: Add dep + install**

In `pyproject.toml` add `"prometheus-client>=0.20"` to `dependencies`.

Run `python -m pip install -e ".[dev]"`.

- [ ] **Step 2: Write `metrics/registry.py`**

```python
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

requests_total = Counter(
    "spillover_requests_total",
    "Total proxy requests",
    labelnames=("project", "provider", "status"),
    registry=REGISTRY,
)

request_duration = Histogram(
    "spillover_request_duration_seconds",
    "Total proxy request duration",
    labelnames=("phase",),
    registry=REGISTRY,
)

overflow_triggered_total = Counter(
    "spillover_overflow_triggered_total",
    "Times the eviction selector returned non-empty",
    labelnames=("project",),
    registry=REGISTRY,
)

episodes_archived_total = Counter(
    "spillover_episodes_archived_total",
    "Episodes inserted into the archive",
    labelnames=("project", "type"),
    registry=REGISTRY,
)

retriever_hits_total = Counter(
    "spillover_retriever_hits_total",
    "Retriever hits attributed to each source",
    labelnames=("project", "source"),
    registry=REGISTRY,
)

facet_queue_depth = Gauge(
    "spillover_facet_queue_depth",
    "Current depth of the facet extraction queue",
    registry=REGISTRY,
)

compaction_detected_total = Counter(
    "spillover_compaction_detected_total",
    "Times the proxy detected client-side compaction",
    labelnames=("project",),
    registry=REGISTRY,
)
```

- [ ] **Step 3: Mount on the app**

In `proxy/app.py`, add:

```python
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from spillover.metrics.registry import REGISTRY


    @app.get("/metrics")
    async def metrics():
        return Response(
            generate_latest(REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
        )
```

(Add `from fastapi.responses import Response` if not already imported.)

Also wire metric increments throughout the existing handlers:
- `requests_total.labels(project_id, provider, str(status_code)).inc()` per request
- `overflow_triggered_total.labels(project_id).inc()` when `_maybe_evict` returns non-empty
- `episodes_archived_total.labels(project_id, "rescued" or "evicted").inc(count)`
- `facet_queue_depth.set(app.state.facet_queue.qsize())` after enqueue
- `compaction_detected_total.labels(project_id).inc(len(rescued))`

- [ ] **Step 4: Tests**

`tests/unit/test_metrics_registry.py`:

```python
from spillover.metrics.registry import REGISTRY, requests_total


def test_counter_increments_and_is_in_registry():
    requests_total.labels(project="p", provider="anthropic", status="200").inc()
    families = list(REGISTRY.collect())
    names = {f.name for f in families}
    assert "spillover_requests" in names or any(
        n.startswith("spillover_requests") for n in names
    )
```

`tests/integration/test_metrics_endpoint.py`:

```python
import pytest
from fastapi.testclient import TestClient

from spillover.proxy.app import create_app


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app) as c:
        yield c


def test_metrics_endpoint_returns_prometheus_text(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    assert "spillover_requests_total" in body or "# HELP spillover" in body
```

- [ ] **Step 5: Run + commit**

```
python -m pytest tests/unit/test_metrics_registry.py tests/integration/test_metrics_endpoint.py -v
```

Expected: 2 PASSED.

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(metrics): Prometheus /metrics endpoint + counter wiring"
```

---

## Phase 4 — Decay scheduler

### Task 5: Background decay task

**Files:**
- Create: `src/spillover/decay/__init__.py` (empty)
- Create: `src/spillover/decay/scheduler.py`
- Create: `tests/unit/test_decay_scheduler.py`
- Modify: `src/spillover/proxy/app.py` (start/stop in lifespan)

- [ ] **Step 1: Write `decay/scheduler.py`**

```python
from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path

from spillover.logging import get_logger
from spillover.storage.sqlite import open_project_db

log = get_logger("decay")

HALF_LIFE_HOURS = {
    "priority": 60 * 24,
    "procedural": 30 * 24,
    "semantic": 14 * 24,
    "episodic": 7 * 24,
}


def _apply_decay_for_project(db_root: Path, project_id: str) -> int:
    """Recompute importance for every vec_episode in this project. Returns count."""
    db = open_project_db(db_root, project_id)
    n = 0
    try:
        rows = db.execute(
            "SELECT episode_id, memory_type, ts FROM vec_episodes "
            "WHERE memory_type IS NOT NULL"
        ).fetchall()
        now_ms = int(time.time() * 1000)
        for r in rows:
            pinned_row = db.execute(
                "SELECT pinned, hit_count FROM episodes WHERE id=?",
                (r["episode_id"],),
            ).fetchone()
            if pinned_row and pinned_row["pinned"] == 1:
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
            hit_count = int(pinned_row["hit_count"]) if pinned_row else 0
            new_imp = min(1.0, base * decay + min(hit_count * 0.05, 0.5))
            db.execute(
                "UPDATE vec_episodes SET importance=? WHERE episode_id=?",
                (new_imp, r["episode_id"]),
            )
            n += 1
    finally:
        db.close()
    return n


class DecayScheduler:
    def __init__(self, db_root: Path, interval_seconds: int = 6 * 3600):
        self.db_root = db_root
        self.interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="decay-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("decay tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                continue

    async def _tick(self) -> None:
        projects_dir = self.db_root / "projects"
        if not projects_dir.exists():
            return
        loop = asyncio.get_running_loop()
        for pdir in projects_dir.iterdir():
            if not pdir.is_dir():
                continue
            pid = pdir.name
            n = await loop.run_in_executor(
                None, _apply_decay_for_project, self.db_root, pid
            )
            if n > 0:
                log.info("decay project=%s updated=%d", pid, n)
```

- [ ] **Step 2: Start/stop in proxy lifespan**

In `app.py` lifespan:

```python
    app.state.decay_scheduler = DecayScheduler(config.db_root)
    app.state.decay_scheduler.start()
    try:
        yield
    finally:
        await app.state.decay_scheduler.stop()
        await app.state.facet_worker.stop()
        await app.state.http_client.aclose()
```

- [ ] **Step 3: Test**

```python
import math
import time

import pytest

from spillover.archive.writer import Turn, archive_raw
from spillover.decay.scheduler import _apply_decay_for_project
from spillover.storage.sqlite import open_project_db


def test_decay_lowers_importance_with_age(tmp_path):
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
                ts=0,  # ancient
            ),
        )
        # Pre-populate vec row
        import struct
        db.execute(
            "INSERT INTO vec_episodes(episode_id, embedding, memory_type, "
            "importance, ts) VALUES (?, ?, ?, ?, ?)",
            (eid, struct.pack("<768f", *[0.0]*768), "episodic", 1.0, 0),
        )
    finally:
        db.close()

    n = _apply_decay_for_project(tmp_path, "p1")
    assert n == 1

    db = open_project_db(tmp_path, "p1")
    try:
        new_imp = db.execute(
            "SELECT importance FROM vec_episodes WHERE episode_id=?", (eid,)
        ).fetchone()[0]
        # ts=0 -> very old -> decay drives importance toward 0
        assert new_imp < 0.5
    finally:
        db.close()


def test_decay_skips_pinned(tmp_path):
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
                ts=0,
            ),
        )
        db.execute("UPDATE episodes SET pinned=1 WHERE id=?", (eid,))
        import struct
        db.execute(
            "INSERT INTO vec_episodes(episode_id, embedding, memory_type, "
            "importance, ts) VALUES (?, ?, ?, ?, ?)",
            (eid, struct.pack("<768f", *[0.0]*768), "episodic", 1.0, 0),
        )
    finally:
        db.close()

    _apply_decay_for_project(tmp_path, "p1")

    db = open_project_db(tmp_path, "p1")
    try:
        imp = db.execute(
            "SELECT importance FROM vec_episodes WHERE episode_id=?", (eid,)
        ).fetchone()[0]
        assert imp == 1.0  # untouched
    finally:
        db.close()
```

- [ ] **Step 4: Run + commit**

```
python -m pytest tests/unit/test_decay_scheduler.py -v
```

Expected: 2 PASSED.

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(decay): background importance-decay scheduler with pinned skip"
```

---

## Phase 5 — Additional wrappers

### Task 6: codex / cursor / continue wrappers

**Files:**
- Create: `src/spillover/wrappers/codex.py`
- Create: `src/spillover/wrappers/cursor.py`
- Create: `src/spillover/wrappers/continue_dev.py`
- Modify: `pyproject.toml` (3 new entry points)
- Create: `tests/unit/test_wrappers_extra.py`

All three follow the same shape as `wrappers/cc.py`. They differ only in:
- Disable env vars (from `env_for(name)`)
- Which CLI they `exec`
- Whether they use the Anthropic or OpenAI base URL

Wrapper template (parametrize via 3 args):

```python
# src/spillover/wrappers/codex.py
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
@click.option("--proxy", default=None)
@click.option("--project", default=None)
@click.pass_context
def main(ctx, proxy: str | None, project: str | None):
    """Launch Codex with spillover wired in."""
    config = Config.from_env()
    cwd = Path.cwd().resolve()
    project_id = project or hashlib.sha1(str(cwd).encode("utf-8")).hexdigest()
    proxy_url = proxy or f"http://127.0.0.1:{config.port}"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = proxy_url
    env["OPENAI_BASE_URL"] = proxy_url
    env.update(env_for("codex"))
    env["SPILLOVER_PROJECT_ID"] = project_id

    cmd = ["codex", *ctx.args]
    click.echo(f"spillover-codex: proxy={proxy_url} project={project_id}")
    completed = subprocess.run(cmd, env=env, check=False)
    sys.exit(completed.returncode)
```

`cursor.py` and `continue_dev.py` follow the same pattern; the only differences are the CLI binary name (`cursor`, `continue` respectively — Continue.dev distributes as a VS Code extension so the wrapper applies env vars at terminal level when developers run `continue` CLI tooling).

- [ ] **Step 1: Write all three wrapper files**

Copy the template above, change the CLI binary name + the `env_for(name)` argument.

- [ ] **Step 2: pyproject entries**

Under `[project.scripts]` add (alongside `spillover` and `spillover-cc`):

```toml
spillover-codex = "spillover.wrappers.codex:main"
spillover-cursor = "spillover.wrappers.cursor:main"
spillover-continue = "spillover.wrappers.continue_dev:main"
```

Re-install: `python -m pip install -e ".[dev]"`.

- [ ] **Step 3: Test**

`tests/unit/test_wrappers_extra.py`:

```python
import sys
from unittest.mock import patch

from click.testing import CliRunner

from spillover.wrappers.codex import main as codex_main
from spillover.wrappers.cursor import main as cursor_main
from spillover.wrappers.continue_dev import main as continue_main


def _run(main, monkeypatch, tmp_path):
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

    target_module = main.__module__.rsplit(".", 1)[0]
    with patch(f"{target_module}.subprocess.run", side_effect=_fake_run):
        with patch.object(sys, "exit"):
            runner.invoke(main, ["--project", "p-test"])
    return captured


def test_codex_wrapper_env(monkeypatch, tmp_path):
    cap = _run(codex_main, monkeypatch, tmp_path)
    assert cap["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999"
    assert cap["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:9999"
    assert cap["env"]["SPILLOVER_PROJECT_ID"] == "p-test"


def test_cursor_wrapper_env(monkeypatch, tmp_path):
    cap = _run(cursor_main, monkeypatch, tmp_path)
    assert "ANTHROPIC_BASE_URL" in cap["env"]
    assert cap["env"]["SPILLOVER_PROJECT_ID"] == "p-test"


def test_continue_wrapper_env(monkeypatch, tmp_path):
    cap = _run(continue_main, monkeypatch, tmp_path)
    assert "ANTHROPIC_BASE_URL" in cap["env"]
    assert cap["env"]["SPILLOVER_PROJECT_ID"] == "p-test"
```

- [ ] **Step 4: Run + commit**

```
python -m pytest tests/unit/test_wrappers_extra.py -v
```

Expected: 3 PASSED.

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(wrapper): codex + cursor + continue wrappers"
```

---

## Phase 6 — A/B benchmark

### Task 7: Benchmark harness

**Files:**
- Create: `src/spillover/bench/__init__.py` (empty)
- Create: `src/spillover/bench/ab.py`
- Modify: `src/spillover/cli.py` (add `spillover bench` subcommand)

The benchmark is a one-shot script. It:
1. Reads N task descriptions from a fixture or stdin.
2. For each task, runs vanilla Claude Code SDK against the Anthropic API directly.
3. For each task, runs the same task against the spillover proxy.
4. Records: total tokens, response length, presence of regression markers (e.g., "I don't remember", "could you remind me", "context lost", etc.) in the model's reply.
5. Writes a markdown report comparing the two runs.

For Plan 4, scope the benchmark to a synthetic version that uses mock provider responses (so it runs offline and proves the harness logic). A real-CLI A/B is left as a manual run.

- [ ] **Step 1: Write `bench/ab.py`**

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunResult:
    task_id: str
    used_spillover: bool
    response: str
    input_tokens: int
    output_tokens: int
    regression_markers: list[str] = field(default_factory=list)


_REGRESSION_PATTERNS = [
    "i don't remember",
    "i do not remember",
    "could you remind",
    "context was lost",
    "i don't have access to the earlier",
    "no longer have the earlier",
    "no recollection",
    "i've forgotten",
    "preciso que voce me lembre",
    "nao me lembro",
]


def _detect_regressions(response: str) -> list[str]:
    text = response.lower()
    return [p for p in _REGRESSION_PATTERNS if p in text]


def summarize_runs(runs: list[RunResult]) -> dict:
    by_mode: dict[bool, list[RunResult]] = {True: [], False: []}
    for r in runs:
        by_mode[r.used_spillover].append(r)
    summary = {}
    for used_spillover, results in by_mode.items():
        if not results:
            continue
        total_in = sum(r.input_tokens for r in results)
        total_out = sum(r.output_tokens for r in results)
        regs = sum(len(r.regression_markers) for r in results)
        summary["spillover" if used_spillover else "vanilla"] = {
            "tasks": len(results),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "regression_markers": regs,
        }
    return summary


def render_markdown(summary: dict) -> str:
    lines = ["# spillover A/B benchmark", "", "| metric | vanilla | spillover |", "|---|---:|---:|"]
    v = summary.get("vanilla", {})
    s = summary.get("spillover", {})
    for key in ("tasks", "total_input_tokens", "total_output_tokens", "regression_markers"):
        lines.append(f"| {key} | {v.get(key, '-')} | {s.get(key, '-')} |")
    return "\n".join(lines) + "\n"


def detect_regressions_for_response(response: str) -> list[str]:
    return _detect_regressions(response)
```

- [ ] **Step 2: Test**

`tests/unit/test_bench_ab.py`:

```python
from spillover.bench.ab import (
    RunResult,
    detect_regressions_for_response,
    render_markdown,
    summarize_runs,
)


def test_detect_regressions_en():
    markers = detect_regressions_for_response("Sorry, I don't remember that earlier message.")
    assert markers


def test_detect_regressions_none():
    markers = detect_regressions_for_response("Sure, here is the code.")
    assert markers == []


def test_summarize_runs_aggregates():
    runs = [
        RunResult(task_id="t1", used_spillover=False, response="ok", input_tokens=100, output_tokens=20),
        RunResult(task_id="t2", used_spillover=False, response="forgot", input_tokens=200, output_tokens=10,
                  regression_markers=["forgot"]),
        RunResult(task_id="t1", used_spillover=True, response="ok", input_tokens=110, output_tokens=20),
    ]
    s = summarize_runs(runs)
    assert s["vanilla"]["tasks"] == 2
    assert s["spillover"]["tasks"] == 1
    assert s["vanilla"]["regression_markers"] == 1
    assert s["spillover"]["regression_markers"] == 0


def test_render_markdown_includes_both_columns():
    runs = [
        RunResult(task_id="t1", used_spillover=False, response="x", input_tokens=10, output_tokens=5),
        RunResult(task_id="t1", used_spillover=True, response="x", input_tokens=12, output_tokens=5),
    ]
    md = render_markdown(summarize_runs(runs))
    assert "| vanilla |" in md
    assert "| spillover |" in md
```

- [ ] **Step 3: Add `spillover bench` CLI subcommand**

In `src/spillover/cli.py` (alongside the other subcommands):

```python
@main.command()
@click.option("--report", type=click.Path(dir_okay=False), default="bench-report.md")
@click.option("--tasks", type=click.Path(exists=True, dir_okay=False), required=False)
def bench(report: str, tasks: str | None):
    """Run the offline A/B benchmark harness and write a markdown report."""
    from spillover.bench.ab import RunResult, render_markdown, summarize_runs

    if not tasks:
        click.echo("No --tasks file provided; nothing to run.")
        return

    import json
    raw = json.loads(Path(tasks).read_text(encoding="utf-8"))
    runs = [RunResult(**r) for r in raw]
    md = render_markdown(summarize_runs(runs))
    Path(report).write_text(md, encoding="utf-8")
    click.echo(f"wrote {report}")
```

(Import `Path` from `pathlib` at the top if not already.)

- [ ] **Step 4: Run + commit**

```
python -m pytest tests/unit/test_bench_ab.py -v
```

Expected: 4 PASSED.

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit -m "feat(bench): offline A/B harness + spillover bench command"
```

---

## Phase 7 — Verify + tag v1.0.0

### Task 8: Final pass

- [ ] **Step 1: Full suite**

```
python -m pytest -v -m "not slow"
python -m pytest -v
```

Expected: ~140 fast PASSED. With slow: ~146 PASSED.

- [ ] **Step 2: Ruff**

```
python -m ruff check src/ tests/
```

Expected: 0 errors.

- [ ] **Step 3: Tag**

```
git -c user.name=luizhcrs -c user.email=luizhcrs@gmail.com commit --allow-empty -m "chore: spillover v1.0.0 (Plan 4 done)"
git tag -a v1.0.0 -m "spillover v1.0.0 - multi-CLI + polish (Plan 4)"
```

---

## Definition of Done

1. All tests across Plans 1–4 pass (≥140 fast, ≥146 with slow).
2. `ruff check src/ tests/` exits 0.
3. OpenAI passthrough route works for non-streaming.
4. Streaming SSE usage rewrite present and opt-outable via env var.
5. `GET /metrics` returns Prometheus text.
6. Decay scheduler reduces importance of aged un-pinned episodes; pinned untouched.
7. 4 wrapper commands resolve on PATH (`spillover-cc`, `spillover-codex`, `spillover-cursor`, `spillover-continue`).
8. `spillover bench` writes a markdown report from a JSON tasks fixture.
9. `v1.0.0` tag exists.
10. All commits authored by `luizhcrs <luizhcrs@gmail.com>`, no `Co-Authored-By` trailers.

End of plan.
