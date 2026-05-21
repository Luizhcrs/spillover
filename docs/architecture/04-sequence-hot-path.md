# 04 — Sequence: inbound request hot path

End-to-end trace of a single `POST /v1/messages` (or `/v1/chat/completions`) routed through spillover.

```mermaid
sequenceDiagram
    autonumber
    participant CLI as Claude Code CLI
    participant Mid as ProjectIdMiddleware
    participant App as HandleInboundRequest
    participant Adp as Adapter (Anthropic/OpenAI)
    participant Ret as Retriever
    participant Prov as Provider API
    participant Evt as EvictionSelector
    participant Arc as Archiver
    participant Q as FacetQueue
    participant M as MetricsSink

    CLI->>+Mid: POST /p/&lt;sha1&gt;/v1/messages
    Mid->>Mid: extract project_id<br/>(path > header > env)
    Mid->>+App: dispatch with state.project_id

    App->>Adp: parse(payload) → Conversation
    App->>App: should_intercept_request?
    Note over App: if compact prompt:<br/>return synthetic 200,<br/>skip Anthropic call

    App->>+Ret: retrieve_ltm_block(conv, project_id)
    Ret->>Ret: embed_text(last 3 turns)
    Ret->>Ret: vector_topk (sqlite-vec)
    Ret->>Ret: extract_entities → graph_walk (Kuzu)
    Ret->>Ret: bm25_topk (FTS5)
    Ret->>Ret: causality_chain (Kuzu AFTER)
    Ret->>Ret: rrf_fuse(4 legs) → top-K
    Ret->>Ret: trim_to_budget
    Ret->>Ret: render_ltm_block
    Ret-->>-App: ltm_text

    App->>Adp: inject_ltm(payload, ltm_text)<br/>placement = between/turns/user/system

    App->>App: detect_compaction(seen_turns diff)
    Note over App: if rescued turns:<br/>archive as compaction_rescued=1,<br/>enqueue facets

    App->>App: record_seen_turns

    App->>+Prov: httpx POST (with retry 3x backoff)<br/>forwarded headers + LTM-injected body
    Prov-->>-App: response (200 + usage)

    App->>Adp: extract_assistant_text
    App->>+Evt: select_for_eviction<br/>(tokens_to_free = new_user + new_assistant)
    Evt->>Evt: Pass 1: FIFO non-priority
    Evt->>Evt: Pass 2: priority fallback
    Evt->>Evt: Pass 3: budget pressure
    Evt-->>-App: SelectionResult

    App->>+Arc: archive_raw for each evicted turn
    Arc->>Arc: sha256 dedup → INSERT episodes<br/>+ INSERT episodes_fts
    Arc-->>-App: episode_ids

    App->>Q: put_nowait(FacetEvent × N)
    Note over Q: bounded 1024,<br/>dropped → facet_dropped_total

    App->>Adp: rewrite_usage<br/>(real - archived = visible)
    App->>M: requests_total++, overflow++,<br/>episodes_archived++

    App-->>-Mid: 200 OK with rewritten usage
    Mid-->>-CLI: response
```

## Phases

| phase | step range | sync/async | notes |
|---|---|---|---|
| Project resolution | 1–2 | sync (middleware) | ~0 ms |
| Adapter parse | 3 | sync | <1 ms |
| Intercept check | 4 | sync, pure | short-circuits before any retrieval if matched |
| Retrieval | 5–13 | sync via executor | embedder cold start can be slow; cached path is fast |
| LTM injection | 14 | sync | payload mutation only |
| Compaction detection + rescue | 15–17 | sync | diff against `seen_turns` |
| Forward | 18–19 | async (httpx) | dominant latency contributor |
| Eviction | 20–22 | sync via executor | 3-pass policy |
| Archive | 23–24 | sync via executor | SQLite WAL write |
| Facet enqueue | 25 | async non-blocking | facet pipeline runs after response |
| Usage rewrite | 26 | sync, pure | `real - archived = visible` |
| Metrics | 27 | sync, lock-free | prometheus_client thread-safe |
| Response | 28–29 | sync | streaming or non-streaming |

## Latency budget (observed, Haiku 4.5 heavy bench)

| phase | budget | observed |
|---|---:|---:|
| Retrieval (cached embedder) | <100 ms p99 | ~50 ms |
| Forward to Anthropic | depends on payload | 2–4 s for 22 k tokens |
| Eviction + archive | <50 ms | ~10–20 ms |
| Total round-trip | matches Anthropic latency | 4.4 s heavy bench |

## Failure modes

| step | failure | handling |
|---|---|---|
| 5–13 | embedder cold-start hang | `_log.exception`; LTM block becomes empty; request still forwards |
| 18–19 | upstream 5xx / timeout | `with_retry` 3x exponential backoff; final fail returns 5xx |
| 18–19 | upstream 4xx | returns 4xx verbatim; no eviction |
| 20–22 | selection pressure (Pass 3) | budget_pressure=True logged; partial eviction kept |
| 23–24 | SQLite UNIQUE violation on hash | dedup path: return existing id |
| 25 | facet queue full | drop + facet_dropped_total counter |
| 27 | metrics counter race | prometheus_client handles concurrency internally |
