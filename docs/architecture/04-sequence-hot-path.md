# 04 — Sequencia: hot path de request inbound

Trace end-to-end de um unico `POST /v1/messages` (ou `/v1/chat/completions`) roteado por spillover.

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
    Mid->>Mid: extrai project_id<br/>(path > header > env)
    Mid->>+App: dispatch com state.project_id

    App->>Adp: parse(payload) → Conversation
    App->>App: should_intercept_request?
    Note over App: se prompt de compact:<br/>retorna 200 sintetico,<br/>pula chamada Anthropic

    App->>+Ret: retrieve_ltm_block(conv, project_id)
    Ret->>Ret: embed_text(ultimos 3 turnos)
    Ret->>Ret: vector_topk (sqlite-vec)
    Ret->>Ret: extract_entities → graph_walk (Kuzu)
    Ret->>Ret: bm25_topk (FTS5)
    Ret->>Ret: causality_chain (Kuzu AFTER)
    Ret->>Ret: rrf_fuse(4 pernas) → top-K
    Ret->>Ret: trim_to_budget
    Ret->>Ret: render_ltm_block
    Ret-->>-App: ltm_text

    App->>Adp: inject_ltm(payload, ltm_text)<br/>placement = between/turns/user/system

    App->>App: detect_compaction(diff seen_turns)
    Note over App: se turnos rescued:<br/>archive como compaction_rescued=1,<br/>enfileira facets

    App->>App: record_seen_turns

    App->>+Prov: httpx POST (com retry 3x backoff)<br/>headers forwarded + body com LTM injected
    Prov-->>-App: response (200 + usage)

    App->>Adp: extract_assistant_text
    App->>+Evt: select_for_eviction<br/>(tokens_to_free = new_user + new_assistant)
    Evt->>Evt: Pass 1: FIFO non-priority
    Evt->>Evt: Pass 2: fallback priority
    Evt->>Evt: Pass 3: budget pressure
    Evt-->>-App: SelectionResult

    App->>+Arc: archive_raw pra cada turno evicted
    Arc->>Arc: dedup sha256 → INSERT episodes<br/>+ INSERT episodes_fts
    Arc-->>-App: episode_ids

    App->>Q: put_nowait(FacetEvent × N)
    Note over Q: bounded 1024,<br/>dropped → facet_dropped_total

    App->>Adp: rewrite_usage<br/>(real - archived = visible)
    App->>M: requests_total++, overflow++,<br/>episodes_archived++

    App-->>-Mid: 200 OK com usage rewritten
    Mid-->>-CLI: response
```

## Fases

| fase | range de passos | sync/async | notas |
|---|---|---|---|
| Resolucao de projeto | 1–2 | sync (middleware) | ~0 ms |
| Parse do adapter | 3 | sync | <1 ms |
| Check de intercept | 4 | sync, pura | curto-circuita antes de retrieval/forward se bater |
| Retrieval | 5–13 | sync via executor | cold start do embedder pode ser lento; cached e rapido |
| Injecao de LTM | 14 | sync | so mutacao de payload |
| Detection + rescue de compaction | 15–17 | sync | diff contra `seen_turns` |
| Forward | 18–19 | async (httpx) | contribuinte dominante de latencia |
| Eviction | 20–22 | sync via executor | politica 3-pass |
| Archive | 23–24 | sync via executor | escrita SQLite WAL |
| Enqueue de facet | 25 | async non-blocking | pipeline de facet roda depois da resposta |
| Usage rewrite | 26 | sync, pura | `real - archived = visible` |
| Metricas | 27 | sync, lock-free | prometheus_client e thread-safe |
| Resposta | 28–29 | sync | streaming ou non-streaming |

## Budget de latencia (observado, heavy bench Haiku 4.5)

| fase | budget | observado |
|---|---:|---:|
| Retrieval (embedder cached) | <100 ms p99 | ~50 ms |
| Forward pra Anthropic | depende do payload | 2–4 s pra 22 k tokens |
| Eviction + archive | <50 ms | ~10–20 ms |
| Round-trip total | bate com latencia da Anthropic | 4.4 s no heavy bench |

## Modos de falha

| passo | falha | tratamento |
|---|---|---|
| 5–13 | embedder pendura no cold start | `_log.exception`; LTM block vira vazio; request ainda forwarda |
| 18–19 | 5xx/timeout do upstream | `with_retry` 3x exponential backoff; falha final retorna 5xx |
| 18–19 | 4xx do upstream | retorna 4xx verbatim; nenhuma eviction |
| 20–22 | pressao de selection (Pass 3) | `budget_pressure=True` logado; eviction parcial mantida |
| 23–24 | violacao UNIQUE no hash do SQLite | path de dedup: retorna id existente |
| 25 | fila de facet cheia | drop + counter facet_dropped_total |
| 27 | race no counter de metricas | prometheus_client trata concorrencia internamente |
