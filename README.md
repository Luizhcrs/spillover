<h1 align="center">spillover</h1>

<p align="center"><em>Agents never compact. They spill over.</em></p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+" /></a>
  <a href="https://github.com/Luizhcrs/spillover/releases/tag/v1.2.0"><img src="https://img.shields.io/badge/release-v1.2.0-green.svg" alt="Release" /></a>
</p>

<p align="center">
  <strong><a href="#spillover-pt-br">Português abaixo</a></strong> &nbsp;|&nbsp;
  <a href="docs/superpowers/specs/2026-05-20-spillover-design.md">Design spec</a> &nbsp;|&nbsp;
  <a href="docs/superpowers/plans/">Plans</a> &nbsp;|&nbsp;
  <a href="#roadmap">Roadmap</a>
</p>

---

## The pain

You give Claude Code a coding task. It reads ten files, runs five commands, makes three decisions, and the conversation hits 170k tokens. The CLI panics and calls `/compact` — it asks the model to summarise the whole thing into a few paragraphs.

> The original intent dies. The exact reason you chose SQLite over Postgres dies. The bug you spent twenty minutes diagnosing in `auth/middleware.py:42` dies. What survives is "we worked on the auth middleware". You start the next turn already lobotomised.

This is **context compaction**, and it is the default behaviour of every long-running agent CLI today. Compaction is **lossy by definition** — running summaries destroy:

- the original intent
- nuance and qualification
- temporal relationships ("decided X *after* trying Y and failing")
- behavioural patterns (`user always prefers …`)
- technical detail that becomes useful later

It is documented in the literature:

- ["Lost in the Middle" (Liu et al., TACL 2024)](https://arxiv.org/abs/2307.03172) — content placed in the middle of a long context is ignored by the LLM's attention. Compaction that re-injects a summary near the middle is doubly lost.
- ["MemGPT: LLMs as Operating Systems" (Packer et al., 2023)](https://arxiv.org/abs/2310.08560) — paging old context out of the active window is the right architectural primitive. MemGPT requires the agent to call memory tools; the agent has to *know it has memory*.
- ["RAG-MCP" (Gan & Sun, 2025)](https://arxiv.org/abs/2505.03275) — empirical evidence that selective retrieval triples tool-selection precision (43.1% → 13.6% without). The same insight applies to past cognition.

Existing memory libraries solve adjacent problems. Mem0 / Zep / Letta / Anthropic Memory tool / Memori remember **facts about you**. [mneme](https://github.com/Luizhcrs/mneme) remembers **what your agent itself can do**. None of them stop the CLI from compacting the conversation in the first place.

spillover fills that gap.

## The solution

spillover sits as a transparent HTTP proxy between any Anthropic- or OpenAI-API client (Claude Code, Codex, Cursor, Continue.dev, raw SDK scripts) and the upstream provider. On every request it does three things the provider does not:

1. **Externalises old turns as raw episodes** when the active context crosses a soft-ceiling watermark. The agent's window stays near max capacity — never compacted, never summarised. Token-balanced 1:1: N new tokens entering means N oldest tokens leaving for the index.
2. **Injects relevant past episodes back as long-term memory** via hybrid retrieval (vector top-K from `sqlite-vec` + k-hop graph walk from Kuzu, fused with Reciprocal Rank Fusion). The agent re-reads its own prior decisions and tool calls as part of every new prompt — with **zero agent-side awareness**.
3. **Defends against client-side compaction.** Most CLIs auto-compact when they perceive context pressure. spillover rewrites the `usage.input_tokens` it returns so the client believes it has headroom, intercepts explicit compact requests, and rescues turns the client drops anyway by diffing inbound conversations against a `seen_turns` table.

### Recommended: install as a native Claude Code plugin

```bash
pip install spillover                                   # daemon binary
# inside Claude Code:
/plugin marketplace add Luizhcrs/spillover
/plugin install spillover@spillover-marketplace
```

The plugin's Setup hook spawns the local proxy daemon (`spillover up`) the first time it runs and the SessionStart hook writes `ANTHROPIC_BASE_URL` plus the disable-compact env vars into `$CLAUDE_ENV_FILE`. From that point on, just type `claude` in any directory — each working directory gets an isolated archive keyed by `sha1(cwd)` and the proxy is fully transparent.

### Manual / non-plugin clients (Codex, Cursor, Continue.dev, raw SDK)

```bash
git clone https://github.com/Luizhcrs/spillover
cd spillover
pip install -e ".[dev]"
spillover up
```

In another terminal, point any client at the proxy:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 \
SPILLOVER_PROJECT_ID=$(pwd | sha1sum | cut -c1-40) \
your-cli
```

Or use one of the bundled wrappers (legacy, prefer the plugin for Claude Code):

```bash
spillover-cc        # Claude Code (legacy wrapper)
spillover-codex     # Codex
spillover-cursor    # Cursor
spillover-continue  # Continue.dev
```

Each wrapper sets `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL`, the disable-compact env vars known for that CLI, and a per-project `SPILLOVER_PROJECT_ID` derived from the current working directory.

## What it looks like in practice

After a few hundred turns of real work, your project DB contains the raw episodes and the graph that links them.

```bash
$ spillover stats abcdef12...
project abcdef12...: episodes: 312
  evicted: 287
  pinned: 4
  embedded: 287
  facet_pending: 0
```

You can interrogate the retriever ad-hoc:

```bash
$ spillover query abcdef12... "why did we drop the legacy auth middleware"
ep_a3f7  score=0.0521  type=priority   source=fusion
ep_5b91  score=0.0488  type=episodic   source=fusion
ep_c204  score=0.0431  type=procedural source=fusion
ep_d617  score=0.0397  type=episodic   source=vector
```

On the very next live turn, the proxy injects those four episodes into a `<spillover-ltm>` block ahead of the system prompt, and the agent reads its own reasoning back — exact quotes, file paths, decisions, error messages — as part of the conversation it is currently in.

```text
<spillover-ltm>
The following are relevant past episodes retrieved from long-term memory.
They are NOT part of the active conversation.

<episode id="ep_a3f7" type="priority" role="assistant">
  decidi remover auth/middleware/legacy.py porque legal flagou storage de
  session tokens fora do escopo do compliance novo. ADR-014.
</episode>

<episode id="ep_5b91" type="episodic" role="user">
  legacy auth tem dependencia no postgres. trocar ordem dos drops na migration
  005 senao quebra foreign key.
</episode>
...
</spillover-ltm>
```

The agent cannot forget what it already decided because the prior decision is right there in front of it again, retrieved by topic, not by prompt-template hope.

## Does it actually work?

The current test suite is **186 tests** across unit + integration, all passing. End-to-end integration covers:

- archive → facet pipeline → vector + graph storage → retrieval on next request → `<spillover-ltm>` in the forwarded payload (`tests/integration/test_retriever_lifecycle.py`)
- token-balanced 1:1 eviction under watermark pressure (`tests/integration/test_eviction_lifecycle.py`)
- intercept of `compact the conversation` short-circuits without forwarding upstream (`tests/integration/test_counter_compact_lifecycle.py`)
- two-request flow rescues turns that disappear when a client compacts anyway (same file)
- streaming SSE `usage.input_tokens` rewrite preserves real value as `spillover_real_input_tokens` for audit (`tests/integration/test_streaming_usage_rewrite.py` + `test_incremental_sse_rewrite.py`)
- Prometheus `/metrics` increments with non-zero counters after live traffic (`tests/integration/test_metrics_wired.py`)
- OpenAI Chat Completions runs the full pipeline parallel to Anthropic (`tests/integration/test_openai_passthrough.py`)

Recall-on-real-task numbers are deliberately not published yet — the Plan 7 work (recall@5 dataset + offline A/B benchmark runner against a small local LLM) is the next milestone. The acceptance-gate dataset will live in this repo and be reproducible offline.

## Run the A/B demo yourself

After installing, run a side-by-side comparison against your real Anthropic
account. Each task is a multi-turn conversation followed by a question that
requires *remembering* the earlier turns.

```bash
# 1. start the proxy
spillover up &

# 2. run A/B (uses OAuth from ~/.claude/.credentials.json if no ANTHROPIC_API_KEY)
spillover bench \
  --tasks src/spillover/bench/tasks_sample.jsonl \
  --report bench-report.md \
  --run

cat bench-report.md
```

You will see two rows per task: `vanilla` (history sent inline) and `spillover` (only the question sent -- history must be recalled via LTM). The report counts:

- how many tasks each mode answered with all the expected anchor strings present
- total tokens spent on each mode
- per-task latency

## What is in the box

- **Local-only.** SQLite + `sqlite-vec` for the vector store, [Kuzu](https://kuzudb.com/) for the property graph, [`fastembed`](https://github.com/qdrant/fastembed) with `nomic-embed-text-v1.5-Q` for embeddings. Zero cloud calls for memory, zero per-MB cost.
- **Provider-agnostic.** Pluggable `Adapter` ABC. Anthropic Messages and OpenAI Chat Completions ship in v1; others added as plugins.
- **Cross-CLI.** Four launcher wrappers in the box (Claude Code, Codex, Cursor, Continue.dev). All set the right env vars and per-project identifier from `cwd`.
- **Per-project isolation.** Each working directory gets its own SQLite + Kuzu pair under `~/.spillover/projects/<sha1(cwd)>/`. Decisions from `agente-imobiliaria` never leak into `bmseletor`.
- **Soft-ceiling architecture.** You decide the operational ceiling (e.g. 500k of Claude Opus's 1M). spillover obeys it as the real cap, leaves a safety buffer to dodge attention degradation, and tunes throughput against provider TPM limits.
- **Five-tier token budget.** Configurable splits for system / working memory / active context / LTM injection / response reserve. Three preset profiles (`coding` / `research` / `conversation`) auto-detected from payload signals.
- **Hybrid retrieval.** Vector top-K + graph k-hop, fused with Reciprocal Rank Fusion ([Cormack et al., SIGIR 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)) and per-type weights (`priority` 1.5, `procedural` 1.2, others 1.0).
- **Weighted-FIFO eviction.** Three-pass selector (FIFO non-priority → priority fallback → budget pressure). Density signal favours keeping turns with tool calls and decisions over verbose repetition.
- **Async facet pipeline.** Bounded queue (1024), executor-offloaded, drops counted via Prometheus. Each archived turn produces an embedding, entity set, decision list, code-ref list, and a 4-way memory-type classification.
- **Counter-compaction defences.** Four vectors: `usage.input_tokens` rewrite (non-streaming + incremental SSE), env-var disable per CLI, intercept of explicit compact prompts, conversation-diff rescue from a `seen_turns` table.
- **Decay.** Importance recomputed every 6 h with type-specific half-lives (procedural 30 d, semantic 14 d, episodic 7 d, priority 60 d). Pinned episodes exempt. Same tick prunes stale `seen_turns` rows past their TTL.
- **Prometheus metrics.** `requests_total`, `request_duration_seconds`, `overflow_triggered_total`, `episodes_archived_total`, `facet_queue_depth`, `facet_dropped_total`, `compaction_detected_total` exposed at `GET /metrics`.

## Architecture

```
                    +---------------------+
   CLI (CC/Codex/   |  spillover-wrapper  |  inject SPILLOVER_PROJECT_ID env
   Cursor/Continue) +----------+----------+  + ANTHROPIC_BASE_URL / OPENAI_BASE_URL
                               | HTTP
                               v
                    +---------------------+
                    |   spillover proxy   |  FastAPI + asyncio + Prometheus
                    |   :8787             |
                    +---------------------+
                       |        ^      |
                  pre  |        |      | post
                       v        |      v
        +--------------+   +----+---+   +---------------+
        | retriever    |   | adapt. |   | overflow      |
        | (RRF fusion) |   | anth/  |   | trigger +     |
        +------+-------+   | openai |   | archiver      |
               |           +---+----+   +-------+-------+
               v               |                v
        +-----------+          v        +---------------+
        | sqlite-vec|   provider real   | facet queue   |
        | + Kuzu    |   (Anthropic /    | asyncio,      |
        | (per-proj)|    OpenAI)        | bounded 1024  |
        +-----------+                   +-------+-------+
               ^                                v
               +-------------- facet extractor (embed, NER, decisions, classifier)
```

## Compared to other memory architectures

| Project | What it does | Resolves compaction loss |
|---|---|:---:|
| [Mem0](https://github.com/mem0ai/mem0) | User-facts memory layer | No (different problem) |
| [Letta / MemGPT](https://github.com/letta-ai/letta) | In-agent tiered memory with self-edit | Partial (agent-side, model-aware) |
| [Zep + Graphiti](https://github.com/getzep/graphiti) | Temporal knowledge graph | Partial (post-hoc, not in path) |
| [Anthropic Memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) | First-party `/memory` directory | No (agent-managed, manual) |
| [mneme](https://github.com/Luizhcrs/mneme) | Capability / affordance memory | No (different problem — what the agent *can do*) |
| **spillover** | Transparent proxy + overflow indexing + injection + counter-compaction | **Yes** |

spillover is **orthogonal** to mneme: mneme injects "you can do these things"; spillover injects "you already decided this, ran this, fixed this". Run both together: mneme is `<capabilities-available>` at the top of the prompt; spillover is `<spillover-ltm>` right below it.

## Configuration

All settings read from env vars; sensible defaults shipped.

| Variable | Default | Purpose |
|---|---|---|
| `SPILLOVER_PORT` | `8787` | proxy listen port |
| `SPILLOVER_OPERATIONAL_CEILING_TOKENS` | `200000` | soft ceiling; all eviction math uses this |
| `SPILLOVER_PROVIDER_MAX_TOKENS` | `2 * ceiling` | informational (real provider window) |
| `SPILLOVER_WATERMARK` | `0.85` | fraction of ceiling that triggers eviction |
| `SPILLOVER_DB_ROOT` | `~/.spillover` | per-project SQLite + Kuzu live here |
| `SPILLOVER_UPSTREAM_BASE_URL` | `https://api.anthropic.com` | |
| `SPILLOVER_OPENAI_BASE_URL` | `https://api.openai.com` | |
| `SPILLOVER_SYSTEM_PCT` | `0.04` | system-prompt budget share |
| `SPILLOVER_WORKING_MEMORY_PCT` | `0.20` | recent-turns reserve |
| `SPILLOVER_ACTIVE_PCT` | `0.50` | bulk conversation |
| `SPILLOVER_LTM_BUDGET_PCT` | `0.15` | LTM injection cap |
| `SPILLOVER_SCRATCHPAD_PCT` | `0.11` | response reserve |
| `SPILLOVER_PROFILE_DEFAULT` | `auto` | `auto` \| `coding` \| `research` \| `conversation` |
| `SPILLOVER_RETRIEVER_TOPK` | `8` | fused hits after RRF |
| `SPILLOVER_RETRIEVER_VECTOR_K` | `50` | vector candidates before fusion |
| `SPILLOVER_RETRIEVER_GRAPH_K` | `50` | graph candidates before fusion |
| `SPILLOVER_STREAM_REWRITE` | `1` | `0` disables streaming usage rewrite |
| `SPILLOVER_LOG_LEVEL` | `INFO` | |
| `SPILLOVER_PROJECT_ID` | (none) | fallback when no `X-Project` header sent |

Budget tiers must sum to `1.0`. Soft ceiling can be set far below the provider window (e.g. 500k of 1M on Opus) to reserve a safety buffer.

## Commands

```bash
spillover up                              # start proxy daemon
spillover stats <project_id>              # episodes / evicted / pinned / embedded / facet_pending
spillover query <project_id> "<text>"     # ad-hoc hybrid retrieval, prints ranked hits
spillover bench --tasks scoring.json      # render A/B markdown report from a scoring file

spillover-cc                              # launch Claude Code wired in
spillover-codex
spillover-cursor
spillover-continue
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/messages` | Anthropic Messages passthrough + LTM + eviction + defences |
| `POST` | `/v1/chat/completions` | OpenAI Chat Completions, same pipeline |
| `GET`  | `/metrics` | Prometheus text exposition |
| `GET`  | `/health` | liveness probe |
| `GET`  | `/` | service banner |

The two POST routes require an `X-Project` HTTP header or a `SPILLOVER_PROJECT_ID` env var on the proxy process. The wrappers set the env var. `/metrics`, `/health`, and `/` are exempt.

## Roadmap

- **Plan 7 — evaluation.** Recall@5 dataset + harness against a local LLM; chaos test that kills the proxy mid-archive and asserts recovery; bench harness becomes a runner instead of a markdown formatter.
- **Plan 8 — retrieval depth.** HyDE query expansion, BM25 lexical leg, Self-RAG retrieval gate, ColBERT late-interaction reranker.
- **Plan 9 — code memory.** AST snapshot per touched file via treesitter, diffs between snapshots, structural change retrieval.
- **Plan 10 — multi-tenant.** Cross-project pool opt-in via tags, tenant_id schema for SaaS deployment.

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Capability cards from mneme-style projects, defense patterns from the literature, and bug reports against the four supported CLIs are encouraged.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

---

<a id="spillover-pt-br"></a>

# spillover (Português)

> Agentes nao compactam. Eles transbordam.

## A dor

Tu manda o Claude Code resolver uma task de codigo. Ele le dez arquivos, roda cinco comandos, toma tres decisoes, e a conversa chega em 170k tokens. O CLI entra em panico e chama `/compact` — pede pro modelo resumir tudo em alguns paragrafos.

> A intencao original morre. O motivo exato de escolher SQLite em vez de Postgres morre. O bug que voce passou vinte minutos diagnosticando em `auth/middleware.py:42` morre. O que sobra e "trabalhamos no middleware de auth". O proximo turno comeca lobotomizado.

Isso e **context compaction**, e e o comportamento padrao de todo agente CLI de uso prolongado hoje. Compaction e **lossy por definicao** — resumos correntes destroem:

- intencao original
- nuance e qualificacao
- relacoes temporais ("decidi X *depois* de tentar Y e falhar")
- padroes comportamentais (`o user sempre prefere …`)
- detalhe tecnico que vira util depois

Esta documentado:

- ["Lost in the Middle" (Liu et al., TACL 2024)](https://arxiv.org/abs/2307.03172) — conteudo no meio de contexto longo e ignorado pela attention. Compaction que reinjeta resumo no meio e duplamente perdido.
- ["MemGPT" (Packer et al., 2023)](https://arxiv.org/abs/2310.08560) — paging old context pra fora da janela ativa e o primitivo arquitetural certo. MemGPT exige que o agente chame tools de memoria; o agente precisa *saber que tem memoria*.
- ["RAG-MCP" (Gan & Sun, 2025)](https://arxiv.org/abs/2505.03275) — empirico: retrieval seletivo triplica precisao de selecao de tool. Mesma insight vale pra cognicao passada.

Bibliotecas de memoria existentes resolvem outro problema. Mem0 / Zep / Letta / Anthropic Memory tool / Memori lembram **fatos sobre voce**. [mneme](https://github.com/Luizhcrs/mneme) lembra **o que o agente pode fazer**. Nenhuma impede o CLI de compactar a conversa.

spillover preenche essa lacuna.

## A solucao

spillover roda como proxy HTTP transparente entre qualquer cliente Anthropic/OpenAI (Claude Code, Codex, Cursor, Continue.dev, scripts SDK) e o provider upstream. Em cada request faz tres coisas que o provider nao faz:

1. **Externaliza turnos antigos como episodios raw** quando o contexto ativo passa de um soft-ceiling watermark. Janela do agente fica colada no teto — nunca compactada, nunca resumida. Token-balanced 1:1: N tokens novos entrando = N tokens mais antigos saindo pro index.
2. **Reinjeta episodios passados relevantes como long-term memory** via retrieval hibrido (top-K vector do `sqlite-vec` + k-hop graph walk do Kuzu, fundidos com Reciprocal Rank Fusion). Agente le sua propria cognicao passada como parte de todo prompt novo — **zero consciencia agent-side**.
3. **Defende contra compaction client-side.** A maior parte dos CLI auto-compacta quando percebe pressao de contexto. spillover reescreve `usage.input_tokens` pra o cliente acreditar que tem folga, intercepta requests explicitos de compact, e resgata turnos que o cliente droppa anyway via diff entre conversa inbound e tabela `seen_turns`.

```bash
git clone https://github.com/Luizhcrs/spillover
cd spillover
pip install -e ".[dev]"
spillover up
```

Em outro terminal:

```bash
spillover-cc
```

Pronto. Todo prompt que teu Claude Code envia agora passa pelo proxy. Overflow indexa, retrieval reinjeta, compaction defendida.

## O que ja funciona

| Capability | Estado |
|---|---|
| Anthropic non-streaming + streaming passthrough | ok |
| OpenAI non-streaming + streaming passthrough | ok |
| SQLite + Kuzu + sqlite-vec per-project | ok |
| Weighted-FIFO eviction 3-pass + token-balance invariant | ok |
| Hybrid retriever (vector + graph + RRF + budget + render) | ok |
| Pipeline facet async (fastembed + regex NER + classifier) | ok |
| Counter-compaction: usage rewrite (non-stream + SSE incremental) | ok |
| Counter-compaction: intercept + rescue via `seen_turns` | ok |
| Soft-ceiling 5-tier budget + dynamic profile | ok |
| Prometheus metrics em toda hot path | ok |
| Decay scheduler com pinned exemption + prune seen_turns | ok |
| 4 wrappers CLI (`cc` / `codex` / `cursor` / `continue`) | ok |
| Bench A/B offline harness | parcial — soh renderiza markdown, runner em Plan 7 |

## Licenca

Apache-2.0. Ver [LICENSE](LICENSE) e [NOTICE](NOTICE).
