# 02 — Containers (C4 Nivel 2)

Dentro do limite do sistema spillover: 4 containers de runtime + 3 stores persistentes.

```mermaid
graph TB
    classDef external fill:#4f7896,stroke:#fff,color:#fff
    classDef container fill:#1168bd,stroke:#fff,color:#fff
    classDef store fill:#2e7d32,stroke:#fff,color:#fff
    classDef person fill:#08427b,stroke:#fff,color:#fff

    user[CLI do dev]:::person

    subgraph spillover_system["limite do sistema spillover"]
        wrapper["Wrapper<br/>spillover-cc /<br/>spillover-codex /<br/>spillover-cursor /<br/>spillover-continue<br/><br/>Click + subprocess"]:::container
        proxy["Daemon do Proxy<br/>:8787<br/><br/>FastAPI + asyncio<br/>+ uvicorn"]:::container
        worker["Facet Worker<br/>asyncio.Queue<br/>maxsize=1024<br/><br/>run_in_executor"]:::container
        decay["Decay Scheduler<br/>cron 6h<br/><br/>asyncio task"]:::container

        sqlite[("SQLite<br/>por projeto<br/>~/.spillover/projects/&lt;pid&gt;/<br/>episodes.db<br/><br/>tabelas: episodes,<br/>seen_turns,<br/>vec_episodes,<br/>episodes_fts")]:::store
        kuzu[("Kuzu<br/>por projeto<br/>~/.spillover/projects/&lt;pid&gt;/kuzu/<br/><br/>5 node tables +<br/>5 relation tables")]:::store
        fastembed[("cache fastembed<br/>~/.cache/fastembed/<br/>nomic-embed-text-v1.5-Q<br/>~130MB ONNX")]:::store
    end

    anthropic[Anthropic API]:::external
    openai[OpenAI API]:::external

    user -->|spawna| wrapper
    wrapper -->|"seta ANTHROPIC_BASE_URL,<br/>OPENAI_BASE_URL,<br/>SPILLOVER_PROJECT_ID,<br/>env vars de disable"| proxy

    proxy -->|"intercept,<br/>retrieve,<br/>archive,<br/>rewrite"| sqlite
    proxy -->|"abre DB do projeto,<br/>graph walk,<br/>causal chain"| kuzu
    proxy -->|"enfileira FacetEvent"| worker
    worker -->|"INSERT vec_episodes,<br/>MERGE Kuzu nodes/edges"| sqlite
    worker --> kuzu
    worker -->|"embed_text"| fastembed
    decay -->|"UPDATE importance,<br/>prune seen_turns"| sqlite
    decay --> kuzu

    proxy -->|"httpx POST<br/>com retry 3x backoff"| anthropic
    proxy --> openai
```

## Containers de runtime

| container | tech | tempo de vida | proposito |
|---|---|---|---|
| Wrapper | Click + subprocess | curto — termina junto com o CLI alvo | seta env vars, lanca CLI |
| Daemon do Proxy | FastAPI + asyncio + uvicorn | longo — 1 processo por maquina | rotas HTTP pra todo trafego forwarded + query ad-hoc |
| Facet Worker | consumidor de asyncio.Queue | vida do proxy | ingestao async dos episodios evicted em embeddings + graph |
| Decay Scheduler | task cron asyncio | vida do proxy | a cada 6h ajusta importance + faz prune de seen_turns velhos |

## Stores persistentes

| store | tech | localizacao | escopo |
|---|---|---|---|
| SQLite | sqlite3 stdlib + sqlite-vec + FTS5 | `~/.spillover/projects/<pid>/episodes.db` | um arquivo por projeto |
| Kuzu | DB de grafo embedded | `~/.spillover/projects/<pid>/kuzu/` | um DB por projeto, cache LRU 32 |
| cache fastembed | modelo ONNX | `~/.cache/fastembed/` | compartilhado entre todos os projetos da maquina |

## Fluxos principais

- **Request inbound**: Wrapper → Proxy → SQLite + Kuzu (leitura) → Anthropic/OpenAI → SQLite (escrita do evicted) → fila do FacetWorker.
- **Pipeline async de facet**: FacetWorker pop → fastembed + classify + extract → SQLite (vec_episodes + fts) + Kuzu (nodes/edges).
- **Sweep do decay**: Scheduler tick → percorre vec_episodes por projeto → UPDATE importance, prune seen_turns velhos.

## Modelo de processo

Um unico processo `spillover up` hospeda proxy + facet worker + decay scheduler no mesmo event loop asyncio. Trabalho CPU-bound (inferencia fastembed, escritas SQLite durante eviction) passa por `loop.run_in_executor` pra o hot path nao bloquear.

## Fronteira de escala

spillover e **single-machine, single-process** por design. Pra deploy multi-tenant SaaS, o schema ja carrega `project_id` (denormalizado em `episodes`), mas o layout de arquivo-por-projeto precisaria consolidar num DB escopado por tenant. Ver [follow-ups do Plan 10](../superpowers/plans/2026-05-21-spillover-plan10-vision-complete.md) pro caminho.
