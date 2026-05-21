# spillover

Transparent LLM proxy with overflow memory architecture.

> Agents never compact. They spill over.

**Status:** v1.1.0 — operational. Closed source. Private.

---

## What spillover does

spillover sits between any Anthropic / OpenAI client (Claude Code, Codex,
Cursor, Continue.dev, raw SDK scripts) and the upstream provider. It does
three things the provider does not:

1. **Externalises old turns as raw episodes** when the active context crosses
   a soft-ceiling watermark. The agent's context stays near max capacity —
   never compacted, never summarised. Token-balanced 1:1 — N tokens in equals
   N oldest tokens out.

2. **Injects relevant past episodes back as long-term memory** via hybrid
   retrieval (vector top-K from `sqlite-vec` + k-hop graph walk from
   `Kuzu`, fused with Reciprocal Rank Fusion). The agent reads its own
   prior decisions and tool calls as part of every new prompt — without
   knowing the proxy exists.

3. **Defends against client-side compaction.** Most CLIs auto-compact when
   they perceive context pressure. spillover rewrites the `usage.input_tokens`
   it returns so the client believes it has headroom, intercepts explicit
   compaction requests, and rescues turns the client drops anyway by diffing
   against a `seen_turns` table.

The architectural opposition is explicit. spillover is the inverse of every
prompt-compression scheme: instead of compressing the conversation into a
smaller representation, it externalises it intact and retrieves on demand.

---

## Install

```bash
git clone https://github.com/Luizhcrs/spillover
cd spillover
pip install -e ".[dev]"
```

Python 3.11+ required. First retrieval call downloads the embedding model
(`nomic-ai/nomic-embed-text-v1.5-Q`, ~130 MB) into the local `fastembed`
cache.

---

## Run

Start the proxy:

```bash
spillover up
```

Default listens on `http://127.0.0.1:8787`. Forwards to
`https://api.anthropic.com` (`/v1/messages`) and `https://api.openai.com`
(`/v1/chat/completions`).

Point any client at it:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 \
SPILLOVER_PROJECT_ID=$(pwd | sha1sum | cut -c1-40) \
claude code
```

Or use one of the bundled wrappers:

```bash
spillover-cc       # Claude Code
spillover-codex    # Codex
spillover-cursor   # Cursor
spillover-continue # Continue.dev
```

Each wrapper sets `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL`, the disable-compact
env vars known for that CLI, and a per-project `SPILLOVER_PROJECT_ID` derived
from the current working directory.

---

## Configuration

All settings come from env vars; sensible defaults shipped.

| Variable | Default | Notes |
|----------|---------|-------|
| `SPILLOVER_PORT` | `8787` | proxy listen port |
| `SPILLOVER_OPERATIONAL_CEILING_TOKENS` | `200000` | soft ceiling; eviction math uses this |
| `SPILLOVER_PROVIDER_MAX_TOKENS` | `2*ceiling` | informational (real provider window) |
| `SPILLOVER_WATERMARK` | `0.85` | fraction of ceiling that triggers eviction |
| `SPILLOVER_DB_ROOT` | `~/.spillover` | per-project SQLite + Kuzu live here |
| `SPILLOVER_UPSTREAM_BASE_URL` | `https://api.anthropic.com` | |
| `SPILLOVER_OPENAI_BASE_URL` | `https://api.openai.com` | |
| `SPILLOVER_SYSTEM_PCT` | `0.04` | budget split |
| `SPILLOVER_WORKING_MEMORY_PCT` | `0.20` | recent turns reserve |
| `SPILLOVER_ACTIVE_PCT` | `0.50` | bulk conversation |
| `SPILLOVER_LTM_BUDGET_PCT` | `0.15` | LTM injection cap |
| `SPILLOVER_SCRATCHPAD_PCT` | `0.11` | response reserve |
| `SPILLOVER_PROFILE_DEFAULT` | `auto` | `auto`/`coding`/`research`/`conversation` |
| `SPILLOVER_RETRIEVER_TOPK` | `8` | fused hits after RRF |
| `SPILLOVER_RETRIEVER_VECTOR_K` | `50` | vector candidates before fusion |
| `SPILLOVER_RETRIEVER_GRAPH_K` | `50` | graph candidates before fusion |
| `SPILLOVER_STREAM_REWRITE` | `1` | `0` to disable streaming usage rewrite |
| `SPILLOVER_LOG_LEVEL` | `INFO` | |
| `SPILLOVER_PROJECT_ID` | (none) | fallback when no `X-Project` header sent |

Budget tiers must sum to 1.0. Soft ceiling can be set far below the provider
window (e.g. 500k of 1M on Opus) to reserve a safety buffer and dodge
attention degradation in the middle of the context.

---

## Commands

```bash
spillover up                              # start proxy
spillover stats <project_id>              # episodes / evicted / pinned / embedded / facet_pending
spillover query <project_id> "<text>"     # ad-hoc hybrid retrieval, prints ranked hits
spillover bench --tasks scoring.json      # render markdown A/B report

spillover-cc                              # launch Claude Code wired in
spillover-codex
spillover-cursor
spillover-continue
```

---

## Architecture

```
                    +---------------------+
   CLI (CC/Codex/   |  spillover-wrapper  |  inject SPILLOVER_PROJECT_ID env
   Cursor/Continue) +----------+----------+  + ANTHROPIC_BASE_URL / OPENAI_BASE_URL
                               | HTTP
                               v
                    +---------------------+
                    |   spillover proxy   |  FastAPI + asyncio
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
        | + Kuzu    |   (Anthropic/     | (asyncio)     |
        | (per proj)|    OpenAI)        +-------+-------+
        +-----------+                           |
               ^                                v
               +-------------- facet extractor (embed, NER, decisions, classifier)
```

Components, each one job:

- `adapters/` — translate Anthropic Messages and OpenAI Chat Completions
  payloads <-> internal `Conversation` dataclass.
- `archive/` — `archive_raw(turn)` writes one row to `episodes` with sha256
  dedup; `UNIQUE(hash)` plus `IntegrityError` retry makes it race-safe.
- `eviction/` — char/4 heuristic tokenizer + 3-pass weighted-FIFO selector
  (FIFO non-priority -> priority fallback -> budget pressure). Density = number
  of structured signals on the turn (tool calls, entities); high-density
  turns evicted last.
- `storage/` — per-project SQLite (with `sqlite-vec` loaded for the
  `vec_episodes` virtual table) + per-project Kuzu graph DB (cached LRU 32,
  schema initialised once per process).
- `facet/` — async worker pulling `FacetEvent` from an `asyncio.Queue`
  (`maxsize=1024`, dropped events counted). For each event: embed via
  fastembed, extract entities (regex), decisions (PT-BR + EN), code refs;
  classify type (`priority`/`procedural`/`semantic`/`episodic`); write to
  `vec_episodes` and graph.
- `retriever/` — vector top-K + graph k-hop, fused via RRF with type weights
  (priority 1.5, procedural 1.2, others 1.0), trimmed to LTM token budget,
  rendered as `<spillover-ltm>` block.
- `counter_compact/` — usage rewrite, compact-request intercept, conversation
  diff rescue (`seen_turns` table), incremental SSE usage rewrite.
- `decay/` — every 6h, recompute importance as
  `base * exp(-age/half_life) + min(hit_count*0.05, 0.5)`. Half-lives:
  procedural 30d, semantic 14d, episodic 7d, priority 60d. Pinned skip.
- `metrics/` — Prometheus counters and gauges; exposed at `GET /metrics`.
- `wrappers/` — Click entry-points for each supported CLI.
- `bench/` — offline A/B harness, markdown report.

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/messages` | Anthropic Messages passthrough + LTM + eviction + defences |
| `POST` | `/v1/chat/completions` | OpenAI Chat Completions, same pipeline |
| `GET` | `/metrics` | Prometheus text exposition |
| `GET` | `/health` | liveness (200) |

The first two require either an `X-Project` HTTP header or a
`SPILLOVER_PROJECT_ID` env var on the proxy process. The wrappers set the env
var. `/metrics`, `/health`, and `/` are exempt.

---

## Status of the v1 product

| Capability | State |
|------------|-------|
| Anthropic non-streaming + streaming passthrough | done |
| OpenAI non-streaming + streaming passthrough | done |
| Per-project SQLite + Kuzu + sqlite-vec | done |
| 3-pass weighted-FIFO eviction with token-balance invariant | done |
| Hybrid retriever (vector + graph + RRF + budget + render) | done |
| Async facet pipeline with fastembed + regex NER + classifier | done |
| Counter-compaction: usage rewrite (non-streaming + incremental SSE) | done |
| Counter-compaction: intercept + `seen_turns` rescue | done |
| Soft-ceiling 5-tier budget + dynamic profile | done |
| Prometheus metrics wired across hot path | done |
| Decay scheduler with pinned exemption | done |
| 4 CLI wrappers (`cc` / `codex` / `cursor` / `continue`) | done |
| Offline A/B benchmark harness | partial — markdown render only, runner stubbed |
| AST snapshot diffs of touched files | not started (v2 candidate) |
| Recall@5 evaluation harness | not started |
| Chaos test (kill mid-archive) | not started |

---

## Roadmap

- **v1.2 / Plan 6** — batch SELECT in retriever + render + decay (kill N+1),
  httpx retry + backoff, redact `Authorization` from logs, prune scheduled
  for `seen_turns`, real bench runner.
- **v1.3 / Plan 7 candidates** — HyDE query expansion, BM25 lexical leg,
  Self-RAG retrieval gate, ColBERT late-interaction reranker.
- **v2 / Plan 8 candidates** — AST snapshot per touched file via treesitter,
  cross-project pool with opt-in tag, multi-tenant tenant_id schema.

---

## Design references

- `docs/superpowers/specs/2026-05-20-spillover-design.md` — full design spec.
- `docs/superpowers/plans/2026-05-20-spillover-mvp-foundation.md` — Plan 1.
- `docs/superpowers/plans/2026-05-21-spillover-retriever.md` — Plan 2.
- `docs/superpowers/plans/2026-05-21-spillover-counter-compaction.md` — Plan 3.
- `docs/superpowers/plans/2026-05-21-spillover-multi-cli-polish.md` — Plan 4.
- `docs/superpowers/plans/2026-05-21-spillover-plan5-soft-ceiling-and-fixes.md` — Plan 5.

---

## Papers spillover engages

- Liu et al. 2024, "Lost in the Middle" (TACL) — directly addresses by
  injecting LTM at high-attention positions.
- Packer et al. 2023, "MemGPT: LLMs as Operating Systems" — same paging
  philosophy without requiring the agent to call tools.
- Gan & Sun 2025, "RAG-MCP" — same retrieval-precision insight applied to
  cognitive episodes instead of tools.
- Cormack et al. 2009, "Reciprocal Rank Fusion" (SIGIR) — used directly in
  the retriever.
- Anti-position vs Rae et al. 2019 ("Compressive Transformer"), Jiang et al.
  2023 ("LongLLMLingua"), Ge et al. 2023 ("ICAE"): spillover preserves raw
  by design.

---

## License

Proprietary. All rights reserved.
