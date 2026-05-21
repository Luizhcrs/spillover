# 02 — Container (C4 Level 2)

Inside the spillover system boundary: four runtime containers + three persistent stores.

```mermaid
graph TB
    classDef external fill:#4f7896,stroke:#fff,color:#fff
    classDef container fill:#1168bd,stroke:#fff,color:#fff
    classDef store fill:#2e7d32,stroke:#fff,color:#fff
    classDef person fill:#08427b,stroke:#fff,color:#fff

    user[Developer CLI]:::person

    subgraph spillover_system["spillover boundary"]
        wrapper["Wrapper<br/>spillover-cc /<br/>spillover-codex /<br/>spillover-cursor /<br/>spillover-continue<br/><br/>Click + subprocess"]:::container
        proxy["Proxy Daemon<br/>:8787<br/><br/>FastAPI + asyncio<br/>+ uvicorn"]:::container
        worker["Facet Worker<br/>asyncio.Queue<br/>maxsize=1024<br/><br/>run_in_executor"]:::container
        decay["Decay Scheduler<br/>cron 6h<br/><br/>asyncio task"]:::container

        sqlite[("SQLite<br/>per-project<br/>~/.spillover/projects/&lt;pid&gt;/<br/>episodes.db<br/><br/>tables: episodes,<br/>seen_turns,<br/>vec_episodes,<br/>episodes_fts")]:::store
        kuzu[("Kuzu<br/>per-project<br/>~/.spillover/projects/&lt;pid&gt;/kuzu/<br/><br/>5 node tables +<br/>5 relation tables")]:::store
        fastembed[("fastembed cache<br/>~/.cache/fastembed/<br/>nomic-embed-text-v1.5-Q<br/>~130MB ONNX")]:::store
    end

    anthropic[Anthropic API]:::external
    openai[OpenAI API]:::external

    user -->|spawns| wrapper
    wrapper -->|"sets ANTHROPIC_BASE_URL,<br/>OPENAI_BASE_URL,<br/>SPILLOVER_PROJECT_ID,<br/>disable env vars"| proxy

    proxy -->|"intercept,<br/>retrieve,<br/>archive,<br/>rewrite"| sqlite
    proxy -->|"open project DB,<br/>graph walk,<br/>causal chain"| kuzu
    proxy -->|"enqueue FacetEvent"| worker
    worker -->|"INSERT vec_episodes,<br/>MERGE Kuzu nodes/edges"| sqlite
    worker --> kuzu
    worker -->|"embed_text"| fastembed
    decay -->|"UPDATE importance,<br/>prune seen_turns"| sqlite
    decay --> kuzu

    proxy -->|"httpx POST<br/>with retry 3x backoff"| anthropic
    proxy --> openai
```

## Runtime containers

| container | tech | lifetime | purpose |
|---|---|---|---|
| Wrapper | Click + subprocess | short — terminates with target CLI | sets env vars, spawns CLI |
| Proxy Daemon | FastAPI + asyncio + uvicorn | long — single process per machine | HTTP routes for all forwarded traffic + ad-hoc query |
| Facet Worker | asyncio.Queue consumer | lifetime of proxy | async ingestion of evicted episodes into embeddings + graph |
| Decay Scheduler | asyncio cron task | lifetime of proxy | every 6h adjust importance + prune stale seen_turns |

## Persistent stores

| store | tech | location | scope |
|---|---|---|---|
| SQLite | stdlib sqlite3 + sqlite-vec + FTS5 | `~/.spillover/projects/<pid>/episodes.db` | one file per project |
| Kuzu | embedded graph DB | `~/.spillover/projects/<pid>/kuzu/` | one DB per project, cached LRU 32 |
| fastembed cache | ONNX model | `~/.cache/fastembed/` | shared across all projects on this machine |

## Key flows

- **Inbound request**: Wrapper → Proxy → SQLite + Kuzu (read) → Anthropic/OpenAI → SQLite (write evicted) → FacetWorker queue.
- **Async facet pipeline**: FacetWorker pop → fastembed + classify + extract → SQLite (vec_episodes + fts) + Kuzu (nodes/edges).
- **Decay sweep**: Scheduler ticks → walks vec_episodes per project → UPDATE importance, prune stale seen_turns.

## Process model

Single `spillover up` process hosts proxy + facet worker + decay scheduler in one asyncio event loop. CPU-bound work (fastembed inference, SQLite writes during eviction) goes through `loop.run_in_executor` so the hot path stays non-blocking.

## Scaling boundary

Spillover is **single-machine, single-process** by design. For multi-tenant SaaS deployment, the schema already carries `project_id` (denormalised in `episodes`), but the file-per-project DB layout would need consolidation into a tenant-scoped DB. See [Plan 10 follow-ups](../superpowers/plans/2026-05-21-spillover-plan10-vision-complete.md) for the path.
