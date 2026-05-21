# 03 — Componentes (C4 Nivel 3): camadas hexagonais

spillover segue o pattern ports-and-adapters (Cockburn): logica de dominio pura no centro, casos de uso da camada de aplicacao orquestrando, inbound adapters dirigindo, outbound adapters servindo.

```mermaid
graph TB
    classDef inbound fill:#1168bd,stroke:#fff,color:#fff
    classDef application fill:#7b1fa2,stroke:#fff,color:#fff
    classDef domain fill:#d84315,stroke:#fff,color:#fff
    classDef outbound fill:#2e7d32,stroke:#fff,color:#fff

    subgraph "INBOUND ADAPTERS (dirigem)"
        http["Rotas FastAPI<br/>POST /v1/messages<br/>POST /v1/chat/completions<br/>GET /metrics<br/>GET /health"]:::inbound
        cli["CLI Click<br/>up / stats / query /<br/>bench / bench-long /<br/>bench-logic / bench-heavy"]:::inbound
        sched["Schedulers<br/>FacetWorker (consumidor de fila)<br/>DecayScheduler (cron 6h)"]:::inbound
        mid["ProjectIdMiddleware<br/>resolve path prefix p/id OU<br/>header X-Project OU<br/>env SPILLOVER_PROJECT_ID"]:::inbound
    end

    subgraph "APLICACAO (casos de uso)"
        handle["HandleInboundRequest<br/>intercept → retrieve →<br/>inject → forward →<br/>rescue → evict → enqueue →<br/>rewrite"]:::application
        facet_uc["ProcessFacets<br/>embed + classify +<br/>extract + index"]:::application
        decay_uc["DecayImportance<br/>+ prune seen_turns"]:::application
        rescue_uc["RescueCompactedTurns<br/>diff + archive como rescued"]:::application
        bench_uc["BenchHarness<br/>A/B vs vanilla"]:::application
    end

    subgraph "DOMINIO (puro)"
        entities["ENTIDADES<br/>Episode, Turn, Hit,<br/>Conversation,<br/>ConversationTurn,<br/>SeenTurn, FacetEvent"]:::domain
        values["VALUE OBJECTS<br/>MemoryType, BudgetProfile,<br/>TokenPlan, SelectionResult,<br/>Entity, Decision, CodeRef"]:::domain
        policies["POLICIES funcoes puras<br/>select_for_eviction 3-pass<br/>rrf_fuse + type weights<br/>formula importance decay<br/>classify 5-way<br/>extract_entities/decisions/<br/>code_refs/open_tasks<br/>diff de compaction<br/>usage_rewrite<br/>sse_rewrite<br/>should_intercept_request"]:::domain
    end

    subgraph "OUTBOUND ADAPTERS (servem)"
        repo[("EpisodeRepo<br/>SQLite<br/>tabela episodes")]:::outbound
        seen[("SeenTurnRepo<br/>SQLite<br/>tabela seen_turns")]:::outbound
        vec[("VectorIndex<br/>sqlite-vec<br/>vec_episodes")]:::outbound
        fts[("LexicalIndex<br/>SQLite FTS5<br/>episodes_fts<br/>tokenchars dot slash underscore dash colon")]:::outbound
        graph[("GraphIndex<br/>Kuzu<br/>cache LRU 32")]:::outbound
        embed["Embedder<br/>fastembed<br/>nomic-embed-text-v1.5-Q"]:::outbound
        anth["AnthropicClient<br/>adapters/anthropic.py<br/>+ httpx + retry"]:::outbound
        oai["OpenAIClient<br/>adapters/openai.py<br/>+ httpx + retry"]:::outbound
        metrics["MetricsSink<br/>prometheus_client"]:::outbound
        log["Logger<br/>stdlib + redact()"]:::outbound
        tok["Tokenizer<br/>heuristica char/4<br/>lru_cache 4096"]:::outbound
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

## Dominio — modulos puros

Zero I/O. Testaveis sem mocks.

| arquivo | conteudo |
|---|---|
| `adapters/base.py` | `Conversation`, `ConversationTurn` |
| `archive/writer.py` | entidade `Turn`, `_hash_turn` |
| `eviction/selector.py` | `ActiveTurn`, `SelectionResult`, `select_for_eviction` (politica 3-pass) |
| `eviction/tokenizer.py` | `count_tokens` (heuristica char/4 + lru_cache) |
| `facet/classifier.py` | `classify` (5-way: priority/procedural/semantic/episodic/task) |
| `facet/entities.py` | `Entity`, `extract_entities` (regex file/url/identifier/command) |
| `facet/decisions.py` | `Decision`, `CodeRef`, `extract_decisions`, `extract_code_refs` |
| `facet/tasks.py` | `has_open_task` (TODO/FIXME/pending PT-BR + EN) |
| `retriever/vector.py` | value object `Hit` |
| `retriever/fusion.py` | `rrf_fuse` + constantes de type-weight |
| `budget/profile.py` | `BudgetProfile`, `select_profile` |
| `budget/plan.py` | `TokenPlan`, `plan_from_config` |
| `counter_compact/usage_rewrite.py` | `rewrite_usage`, `rewrite_response_json` |
| `counter_compact/sse_rewrite.py` | `rewrite_sse_body`, `has_usage_marker` |
| `counter_compact/intercept.py` | `should_intercept_request`, `make_intercept_response` |
| `counter_compact/detection.py` (helpers de hash) | `_hash_assistant_message` |
| `decay/scheduler.py` (constantes + formula) | `HALF_LIFE_HOURS`, `_base_importance` |

## Aplicacao — casos de uso

Orquestram policies de dominio atraves de ports outbound.

| caso de uso | onde mora |
|---|---|
| HandleInboundRequest | `proxy/app._handle_request` |
| ProcessFacets | `facet/worker._process_one` |
| DecayImportance | `decay/scheduler._apply_decay_for_project` |
| RescueCompactedTurns | `counter_compact/detection.detect_compaction` + bloco de rescue do proxy |
| AdHocRetrieval | `cli.query` |
| BenchHarness | `bench/runner` + `bench/long_conversation` + `bench/landing_page_scenario` + `bench/heavy_stress` |

## Inbound — adapters dirigentes

| adapter | tech | o que dirige |
|---|---|---|
| Proxy FastAPI | FastAPI + asyncio + lifespan | rotas HTTP pra /v1/messages, /v1/chat/completions, /metrics, /health, / |
| `ProjectIdMiddleware` | middleware Starlette | resolucao de project_id (path > header > env) |
| CLI Click | Click + uvicorn | up / stats / query / bench / bench-long / bench-logic / bench-heavy |
| Loop de decay | asyncio task no lifespan | DecayImportance a cada 6h |
| Facet worker | consumidor asyncio.Queue | ProcessFacets por evento dequeued |
| Wrappers | Click + subprocess | spillover-cc / -codex / -cursor / -continue |

## Outbound — adapters servientes

| port | adapter | tech |
|---|---|---|
| EpisodeRepo | `archive/writer.archive_raw` + `storage/sqlite.open_project_db` | SQLite (WAL, UNIQUE hash) |
| SeenTurnRepo | `counter_compact/detection.record_seen_turns` + `prune_old_seen_turns` | SQLite (PK composta) |
| VectorIndex | `retriever/vector.vector_topk` + escritas do `facet/worker` | tabela virtual sqlite-vec |
| LexicalIndex | `retriever/lexical.bm25_topk` + INSERT do `archive/writer` | SQLite FTS5 |
| GraphIndex | `retriever/graph.graph_walk` + `retriever/causal.causality_chain` + MERGE do `facet/worker` | Kuzu embedded, LRU 32 |
| Embedder | `facet/embed.embed_text` | fastembed (ONNX nomic-embed-text-v1.5-Q, 768 dim) |
| Tokenizer | `eviction/tokenizer.count_tokens` | heuristica char/4 memoizada lru_cache 4096 |
| ProviderClient (Anthropic) | `adapters/anthropic.AnthropicAdapter` + httpx em `proxy/app` | httpx → api.anthropic.com /v1/messages |
| ProviderClient (OpenAI) | `adapters/openai.OpenAIAdapter` + httpx em `proxy/app` | httpx → api.openai.com /v1/chat/completions |
| Retry ProviderClient | `proxy/retry.with_retry` | exponential backoff 3x em 429/5xx/timeout |
| MetricsSink | `metrics/registry` | prometheus_client (Counter/Gauge/Histogram) |
| Logger | `logging.get_logger` + `redact()` | logging stdlib com redaction de header |

## Desvios pragmaticos do hexagonal estrito

spillover e hexagonal-por-feature, nao hexagonal-por-camada. Desvios:

1. `proxy/app.py` mistura camadas — `_retrieve_ltm_block` chama outbound adapters direto sem interface intermediaria.
2. `facet/worker._process_one` chama `open_project_db`, `open_project_kuzu`, `embed_text` direto.
3. `Tokenizer` e `Clock` nao tem port formal — usados via imports stdlib direto.
4. A palavra "adapter" em `adapters/anthropic.py` e `adapters/openai.py` significa *adapter de wire-format* (parse JSON ↔ Conversation), nao *outbound adapter hexagonal*.
5. Sem container de DI; dependencias resolvem via imports module-level.

Sao desvios deliberados. Refactor estrito pra hexagonal e um [follow-up](../superpowers/plans/) de ~3-4 dias, soh justificado se a codebase passar de ~5k LOC ou o modelo de deploy mudar pra SaaS multi-tenant.
