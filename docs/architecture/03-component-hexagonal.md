# 03 — Component (C4 Level 3): Hexagonal layers

Spillover follows the ports-and-adapters pattern (Cockburn): pure domain logic at the centre, application use cases orchestrating it, inbound adapters driving the system, outbound adapters serving it.

```mermaid
graph TB
    classDef inbound fill:#1168bd,stroke:#fff,color:#fff
    classDef application fill:#7b1fa2,stroke:#fff,color:#fff
    classDef domain fill:#d84315,stroke:#fff,color:#fff
    classDef outbound fill:#2e7d32,stroke:#fff,color:#fff

    subgraph "INBOUND ADAPTERS (driving)"
        http["FastAPI Routes<br/>POST /v1/messages<br/>POST /v1/chat/completions<br/>GET /metrics<br/>GET /health"]:::inbound
        cli["Click CLI<br/>up / stats / query /<br/>bench / bench-long /<br/>bench-logic / bench-heavy"]:::inbound
        sched["Schedulers<br/>FacetWorker (queue consumer)<br/>DecayScheduler (6h cron)"]:::inbound
        mid["ProjectIdMiddleware<br/>resolves /p/&lt;id&gt; OR<br/>X-Project header OR<br/>SPILLOVER_PROJECT_ID env"]:::inbound
    end

    subgraph "APPLICATION (use cases)"
        handle["HandleInboundRequest<br/>intercept → retrieve →<br/>inject → forward →<br/>rescue → evict → enqueue →<br/>rewrite"]:::application
        facet_uc["ProcessFacets<br/>embed + classify +<br/>extract + index"]:::application
        decay_uc["DecayImportance<br/>+ prune seen_turns"]:::application
        rescue_uc["RescueCompactedTurns<br/>diff + archive as rescued"]:::application
        bench_uc["BenchHarness<br/>A/B vs vanilla"]:::application
    end

    subgraph "DOMAIN (pure)"
        entities["<b>Entities</b><br/>Episode, Turn, Hit,<br/>Conversation,<br/>ConversationTurn,<br/>SeenTurn, FacetEvent"]:::domain
        values["<b>Value Objects</b><br/>MemoryType, BudgetProfile,<br/>TokenPlan, SelectionResult,<br/>Entity, Decision, CodeRef"]:::domain
        policies["<b>Policies (pure fns)</b><br/>select_for_eviction (3-pass)<br/>rrf_fuse + type weights<br/>importance decay formula<br/>classify (5-way)<br/>extract_entities/decisions/<br/>code_refs/open_tasks<br/>compaction diff<br/>usage_rewrite<br/>sse_rewrite<br/>should_intercept_request"]:::domain
    end

    subgraph "OUTBOUND ADAPTERS (driven)"
        repo[("EpisodeRepo<br/>SQLite<br/>episodes table")]:::outbound
        seen[("SeenTurnRepo<br/>SQLite<br/>seen_turns table")]:::outbound
        vec[("VectorIndex<br/>sqlite-vec<br/>vec_episodes")]:::outbound
        fts[("LexicalIndex<br/>SQLite FTS5<br/>episodes_fts<br/>tokenchars ./_-:")]:::outbound
        graph[("GraphIndex<br/>Kuzu<br/>cached LRU 32")]:::outbound
        embed["Embedder<br/>fastembed<br/>nomic-embed-text-v1.5-Q"]:::outbound
        anth["AnthropicClient<br/>adapters/anthropic.py<br/>+ httpx + retry"]:::outbound
        oai["OpenAIClient<br/>adapters/openai.py<br/>+ httpx + retry"]:::outbound
        metrics["MetricsSink<br/>prometheus_client"]:::outbound
        log["Logger<br/>stdlib + redact()"]:::outbound
        tok["Tokenizer<br/>char/4 heuristic<br/>lru_cache 4096"]:::outbound
    end

    http --> mid
    mid --> handle
    cli --> bench_uc
    cli --> decay_uc
    sched --> facet_uc
    sched --> decay_uc

    handle --> policies
    handle --> repo
    handle --> seen
    handle --> vec
    handle --> fts
    handle --> graph
    handle --> embed
    handle --> tok
    handle --> anth
    handle --> oai
    handle --> metrics
    handle --> log

    facet_uc --> policies
    facet_uc --> repo
    facet_uc --> vec
    facet_uc --> fts
    facet_uc --> graph
    facet_uc --> embed

    decay_uc --> policies
    decay_uc --> repo
    decay_uc --> vec
    decay_uc --> seen

    rescue_uc --> policies
    rescue_uc --> repo
    rescue_uc --> seen

    bench_uc --> anth
    bench_uc --> oai
```

## Domain — pure modules

Zero I/O. Tested without mocks.

| file | content |
|---|---|
| `adapters/base.py` | `Conversation`, `ConversationTurn` |
| `archive/writer.py` | `Turn` entity, `_hash_turn` |
| `eviction/selector.py` | `ActiveTurn`, `SelectionResult`, `select_for_eviction` (3-pass policy) |
| `eviction/tokenizer.py` | `count_tokens` (char/4 heuristic + lru_cache) |
| `facet/classifier.py` | `classify` (5-way: priority/procedural/semantic/episodic/task) |
| `facet/entities.py` | `Entity`, `extract_entities` (file/url/identifier/command regex) |
| `facet/decisions.py` | `Decision`, `CodeRef`, `extract_decisions`, `extract_code_refs` |
| `facet/tasks.py` | `has_open_task` (TODO/FIXME/pending PT-BR + EN) |
| `retriever/vector.py` | `Hit` value object |
| `retriever/fusion.py` | `rrf_fuse` + type-weight constants |
| `budget/profile.py` | `BudgetProfile`, `select_profile` |
| `budget/plan.py` | `TokenPlan`, `plan_from_config` |
| `counter_compact/usage_rewrite.py` | `rewrite_usage`, `rewrite_response_json` |
| `counter_compact/sse_rewrite.py` | `rewrite_sse_body`, `has_usage_marker` |
| `counter_compact/intercept.py` | `should_intercept_request`, `make_intercept_response` |
| `counter_compact/detection.py` (hash helpers) | `_hash_assistant_message` |
| `decay/scheduler.py` (constants + formula) | `HALF_LIFE_HOURS`, `_base_importance` |

## Application — use cases

Orchestrate domain policies through outbound ports.

| use case | implementation locus |
|---|---|
| HandleInboundRequest | `proxy/app._handle_request` |
| ProcessFacets | `facet/worker._process_one` |
| DecayImportance | `decay/scheduler._apply_decay_for_project` |
| RescueCompactedTurns | `counter_compact/detection.detect_compaction` + proxy rescue block |
| AdHocRetrieval | `cli.query` |
| BenchHarness | `bench/runner` + `bench/long_conversation` + `bench/landing_page_scenario` + `bench/heavy_stress` |

## Inbound — driving adapters

| adapter | tech | what it drives |
|---|---|---|
| FastAPI proxy | FastAPI + asyncio + lifespan | HTTP routes for /v1/messages, /v1/chat/completions, /metrics, /health, / |
| `ProjectIdMiddleware` | Starlette middleware | project_id resolution (path > header > env) |
| Click CLI | Click + uvicorn | up / stats / query / bench / bench-long / bench-logic / bench-heavy |
| Decay loop | asyncio task in lifespan | DecayImportance every 6h |
| Facet worker | asyncio.Queue consumer | ProcessFacets per dequeued event |
| Wrappers | Click + subprocess | spillover-cc / -codex / -cursor / -continue |

## Outbound — driven adapters

| port | adapter | tech |
|---|---|---|
| EpisodeRepo | `archive/writer.archive_raw` + `storage/sqlite.open_project_db` | SQLite (WAL, UNIQUE hash) |
| SeenTurnRepo | `counter_compact/detection.record_seen_turns` + `prune_old_seen_turns` | SQLite (composite PK) |
| VectorIndex | `retriever/vector.vector_topk` + `facet/worker` writes | sqlite-vec virtual table |
| LexicalIndex | `retriever/lexical.bm25_topk` + `archive/writer` INSERT | SQLite FTS5 |
| GraphIndex | `retriever/graph.graph_walk` + `retriever/causal.causality_chain` + `facet/worker` MERGE | Kuzu embedded, LRU 32 |
| Embedder | `facet/embed.embed_text` | fastembed (nomic-embed-text-v1.5-Q ONNX, 768 dim) |
| Tokenizer | `eviction/tokenizer.count_tokens` | char/4 heuristic memoised lru_cache 4096 |
| ProviderClient (Anthropic) | `adapters/anthropic.AnthropicAdapter` + httpx in `proxy/app` | httpx → api.anthropic.com /v1/messages |
| ProviderClient (OpenAI) | `adapters/openai.OpenAIAdapter` + httpx in `proxy/app` | httpx → api.openai.com /v1/chat/completions |
| ProviderClient retry | `proxy/retry.with_retry` | exponential backoff 3x on 429/5xx/timeout |
| MetricsSink | `metrics/registry` | prometheus_client (Counter/Gauge/Histogram) |
| Logger | `logging.get_logger` + `redact()` | stdlib logging with header redaction |

## Pragmatic deviations from strict hexagonal

Spillover is hexagonal-by-feature, not by-layer. Honest deviations:

1. `proxy/app.py` mixes layers — `_retrieve_ltm_block` calls outbound adapters directly without going through an explicit port interface.
2. `facet/worker._process_one` calls `open_project_db`, `open_project_kuzu`, `embed_text` directly.
3. `Tokenizer` and `Clock` have no formal port — used via direct stdlib imports.
4. The word "adapter" in `adapters/anthropic.py` and `adapters/openai.py` means *wire-format adapter* (parse JSON ↔ Conversation), not *hexagonal outbound adapter*.
5. No DI container; dependencies resolve via module-level imports.

These are deliberate. Strict hexagonal refactor is a [follow-up](../superpowers/plans/) worth ~3–4 days, only justified if the codebase grows past ~5k LOC or the deployment model changes to multi-tenant SaaS.
