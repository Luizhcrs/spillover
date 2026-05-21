# spillover — design spec

**Date:** 2026-05-20
**Status:** Draft (awaiting approval)
**Author:** Luiz Henrique Cavalcanti Ramos da Silva
**Slogan:** "Agents never compact. They spill over."

---

## 1. Problem

LLM agents (Claude Code, Codex, Cursor, Continue.dev, custom SDK scripts) hit context limits and trigger automatic compaction. Compaction destroys semantic substance: original intent, nuances, temporal relations, behavior patterns, technical detail that becomes useful later. The agent loses continuity, identity, and decision history within and across sessions.

Existing memory libraries solve adjacent but different problems:

- **mneme** — affordance memory (which tools the agent has)
- **claude-mem** — Claude Code session capture only
- **Mem0 / Zep / Letta / Memori / Anthropic Memory tool** — facts about the user

None of them solve the core architectural failure: **context compression itself**.

## 2. Vision

Replace compaction with **overflow indexing**:

- Active context stays near max capacity (e.g., 170k–190k of 200k tokens).
- When the window fills past a watermark, the oldest blocks **spill over** into a persistent index — raw, never summarized.
- A hybrid retriever (vector + graph + temporal + importance) injects the most relevant past blocks into the next input.
- The agent never loses original intent. The model never depends on imperfect summaries.

This is **persistent cognition**, not a chatbot with a bigger window.

## 3. Scope (v1)

- Transparent LLM proxy intercepts requests to Anthropic Messages API and OpenAI Chat Completions API (adapters are pluggable; the wire-protocol is provider-agnostic by design).
- Per-project memory store (one SQLite + Kuzu database per `cwd` hash).
- Episodic memory of agent cognition: every overflowed turn preserved raw, indexed by facets.
- Hybrid retrieval (vector + graph + temporal recency + importance) with Reciprocal Rank Fusion.
- Counter-compaction defenses (prevent the CLI from compacting locally).
- Cross-CLI: works with Claude Code, Codex, Cursor, Continue.dev, raw SDK scripts.

**Out of scope (v1):**

- AST snapshot diffs of touched files (v2).
- Cross-project knowledge transfer / global pool (v2 opt-in).
- Multi-tenant SaaS deployment (v2; schema already carries `project_id`, easy to extend).
- Fine-tuning custom embedders (uses `nomic-embed-text-v1.5-Q` via fastembed).
- Mobile / IDE-extension wrappers (v2).

## 4. Architecture

```
                    +---------------------+
   CLI agent  -->   |  spillover-wrapper  |  injects X-Project=<hash(cwd)>
   (CC/Codex/etc)   +----------+----------+  + real Authorization
                               | HTTP
                               v
                    +---------------------+
                    |   spillover proxy   |  FastAPI + asyncio
                    |   (default :8787)   |
                    +---------------------+
                       |        ^      |
                  pre  |        |      | post (stream)
                       v        |      v
        +--------------+   +----+---+   +---------------+
        | retriever    |   | adapt. |   | overflow      |
        | (fusion)     |   | anth/  |   | trigger +     |
        +------+-------+   | openai |   | archiver      |
               |           +---+----+   +-------+-------+
               v               |                v
        +-----------+          v        +---------------+
        | sqlite-vec|   provider real   | indexer queue |
        | + Kuzu    |   (Anthropic/     | (background)  |
        | (per proj)|    OpenAI/...)    +-------+-------+
        +-----------+                           |
               ^                                v
               +--------------- facet extractor (embed, NER, decisions)
```

### 4.1 Components

1. **wrapper** — per-CLI shim. Sets `X-Project=<sha1(cwd)>`, forwards `Authorization`, runs the CLI under modified env vars. Examples: `spillover-cc`, `spillover-codex`. Selects the right adapter by inspecting the CLI's outbound base URL.

2. **proxy core** — receives the request, identifies `project_id`, calls retriever, builds final payload, calls provider via adapter, captures response stream, measures tokens, schedules overflow if needed.

3. **adapter** — per-provider module. Translates spillover internal `Conversation` ↔ wire format (Anthropic Messages, OpenAI Chat Completions). MVP ships 2 adapters; new providers added as plugins.

4. **retriever** — hybrid fusion:
   - vector top-50 from sqlite-vec
   - graph k-hop from Kuzu (k=2, limit 50)
   - temporal recency boost
   - importance score
   - Reciprocal Rank Fusion (RRF) with type weights (procedural >= episodic >= semantic)
   - filtered to LTM token budget

5. **overflow trigger** — post-response: measures `total_tokens / window_max`. Past watermark (default 0.85), selects oldest turns (FIFO after system + recent buffer + pinned) until back to 0.70. Packages and pushes to indexer queue.

6. **archiver** — writes raw turn to SQLite (`episodes` table), returns `episode_id` synchronously to guarantee durability before async pipeline begins.

7. **facet extractor (async worker)** — pulls `episode_id`, runs:
   - embedding (fastembed `nomic-embed-text-v1.5-Q`)
   - NER (spaCy lite + regex)
   - decisions / code_refs / tool_calls extractor
   - 4-way classifier (procedural / episodic / semantic / emotional)
   - writes to sqlite-vec + Kuzu (nodes + edges)

8. **decay scheduler** — cron every 6h. Adjusts `importance = base * exp(-age/half_life) + min(hit_count*0.05, 0.5)`. Half-life by type: procedural 30d, semantic 14d, episodic 7d, emotional 60d. Pinned skip decay.

### 4.2 Final input payload to LLM

```
[SYSTEM original from client]
[<spillover-ltm> top-K episodes rendered </spillover-ltm>]
[ACTIVE CONTEXT (raw turns alive)]
[USER new message]
```

LTM injection delivered as additional system message OR system block (adapter decides). Tagged so the adapter can exclude it from prompt-cache hash computation.

### 4.3 Token budget defaults (configurable per project)

| Slice | Default |
|------|--------:|
| Active context | 70% |
| LTM injection | 15% |
| Response reservation | 10% |
| System + tools | 5% |

Overflow watermark default: trigger at 0.85, evict down to 0.70.

## 5. Counter-compaction defenses

Auto-compaction is a client-side decision. Spillover must neutralize it.

| Vector | Mechanism | Default |
|------|------|------|
| 1. usage rewrite | Response `usage.input_tokens` reflects post-overflow size (CLI subtracts from local budget, believes it has headroom) | active |
| 2. env-var disable | Wrapper exports `CLAUDE_CODE_AUTO_COMPACT=0` and analogs before spawning CLI | active |
| 3. fake window size | Inject larger context-window value in response header so CLI fraction-based heuristics never fire | opt-in per CLI |
| 4. intercept /compact | Detect explicit compaction tool calls, ack without forwarding | active when pattern known |

If the CLI compacts anyway, spillover detects via conversation diff (turn N has msgs A,B,C; turn N+1 has summary). Marks `compaction_detected`, rescues raw as episodes `rescued_from_compaction`, re-injects via next LTM block, alerts user.

## 6. Data flow

### 6.1 Hot path (request)

1. CLI sends `POST /v1/messages` with conversation + `X-Project`, real `Authorization`.
2. middleware opens SQLite + Kuzu handles for that project.
3. adapter parses payload into internal `Conversation`.
4. retriever queries (last 10 msgs + new user as query):
   - embed query (~25ms)
   - vector top-50 (~5ms)
   - entity extract → seeds (~10ms)
   - graph k-hop (~15ms)
   - temporal boost (~1ms)
   - RRF fusion → top-K=8 (~5ms)
   - filter by LTM token budget
5. adapter builds final payload.
6. httpx streams to provider, chunks pass through to CLI.
7. on stream end:
   - capture full assistant response
   - capture real upstream usage
   - rewrite `usage.input_tokens` = real - tokens_archived_pending
   - send terminal SSE chunk with visible usage
8. background: enqueue `AssistantTurnComplete(project_id, conversation_snapshot, response, real_usage, visible_usage)`.

### 6.2 Cold path (overflow async)

1. Worker consumes `AssistantTurnComplete`.
2. Compute `fill_ratio = real_usage.input_tokens / window_max`. If < 0.85: persist turn as active-record, skip overflow.
3. If >= 0.85: `select_for_eviction(target_ratio=0.70)`. Excludes system, last 4 turns (recent buffer), pinned. FIFO over the rest until back to 0.70.
4. For each eviction candidate: `archive_raw` (INSERT into `episodes` table). Durability before marking removable.
5. Mark episodes `evicted` — next retriever query excludes them from active set.
6. Emit `FacetExtractEvent(episode_id)` to facet queue.

### 6.3 Facet pipeline

For each `episode_id`:

- embedding = fastembed.embed(content)
- entities = ner(content)
- decisions = decision_parser(content)
- code_refs = code_ref_parser(tool_calls_json)
- memory_type = classifier(content, tool_calls) → {procedural | episodic | semantic | emotional}
- importance = base_score(type, entities_count, decisions_count, tool_calls_count)
- writes:
  - sqlite-vec row: `vec_episodes(episode_id, embedding, type, importance, ts)`
  - Kuzu MERGE: `Episode`, `Entity`, `File`, `Decision`, `Command`, edges `MENTIONS / TOUCHED / IMPLEMENTS / RAN / AFTER`

### 6.4 Decay

Cron every 6h iterates `vec_episodes`:

```
age_hours = now - ts
half_life = type_to_halflife[memory_type]
new_imp = base * exp(-age_hours/half_life) + min(hit_count * 0.05, 0.5)
if pinned: skip
UPDATE vec_episodes SET importance=new_imp
```

### 6.5 Re-hit promotion

When retriever includes an episode in top-K and the next assistant response references the injected content (high cosine similarity between LTM block and response chunks), increment `hit_count`, log `PromotionEvent`.

## 7. Schema

### 7.1 SQLite — `episodes` (per project)

| col | type | notes |
|------|------|------|
| `id` | TEXT PK | uuid7 |
| `project_id` | TEXT | denormalized (proj DB anyway) |
| `role` | TEXT | user/assistant/tool |
| `content_json` | TEXT | full raw content blocks |
| `tool_calls_json` | TEXT | structured tool_use / tool_result |
| `code_refs_json` | TEXT | parsed `[{path, line, op}]` |
| `ts` | INTEGER | unix epoch ms |
| `hash` | TEXT | sha256 dedup |
| `evicted` | INTEGER | 0/1 |
| `pinned` | INTEGER | 0/1 |
| `hit_count` | INTEGER | default 0 |
| `compaction_rescued` | INTEGER | 0/1 |

### 7.2 sqlite-vec — `vec_episodes`

| col | type |
|------|------|
| `episode_id` | TEXT FK |
| `embedding` | FLOAT[768] |
| `memory_type` | TEXT |
| `importance` | REAL |
| `ts` | INTEGER |

### 7.3 Kuzu graph (per project)

Nodes:

- `Episode {id, ts, memory_type, importance}`
- `Entity {name, kind}`
- `File {path, ext}`
- `Decision {hash, summary}`
- `Command {sig, first_seen_ts}`

Edges:

- `(Episode)-[:MENTIONS]->(Entity)`
- `(Episode)-[:TOUCHED]->(File)`
- `(Episode)-[:IMPLEMENTS]->(Decision)`
- `(Episode)-[:RAN]->(Command)`
- `(Episode)-[:AFTER]->(Episode)` (temporal chain within project)

## 8. Error handling

| Failure | Detect | Response | Recovery |
|------|------|------|------|
| Provider 5xx/timeout | httpx exception | retry 3x backoff (1s,4s,16s) | final fail = forward error, do not archive turn |
| Provider 4xx | status | forward raw | log, no archive |
| Provider 429 | header | wait + retry 1x | persist? forward |
| Embedder model missing | fastembed load fail | retriever no-op | background download, warn |
| sqlite-vec query fail | DB exception | retriever no-op | log, hot path continues without LTM |
| Kuzu fail | DB exception | retriever uses vector only | log, isolate query |
| Archive write fail | sqlite IOError | do NOT mark removable, retry indefinitely | next request reuses raw |
| Facet extractor crash | worker traceback | episode raw already safe, mark `facet_pending=true` | retry on boot + cron 1h |
| Counter-compact failed | conversation diff turn N vs N+1 | event, rescue raw, mark `rescued_from_compaction` | reinject in next LTM block, alert |
| Disk full | os errno | block archive, return 503 | alert + suggest prune |

## 9. Observability

Prometheus metrics at `/metrics`:

- `spillover_requests_total{project,provider,model,status}` counter
- `spillover_request_duration_seconds{phase}` histogram (phase = retrieve|adapt|provider|archive)
- `spillover_overflow_triggered_total{project}` counter
- `spillover_episodes_archived_total{project,type}` counter
- `spillover_retriever_hits_total{project,source}` counter
- `spillover_retriever_topk_episodes_avg` gauge
- `spillover_facet_queue_depth` gauge
- `spillover_facet_extract_duration_seconds` histogram
- `spillover_compaction_detected_total{project,cli}` counter (alarm)
- `spillover_token_budget_active_pct` gauge
- `spillover_token_budget_ltm_pct` gauge

Structured JSON logs to stderr. Keys: `event`, `project`, `episode_id`, `latency_ms`, `error`.

CLI utilities:

- `spillover up [--project DIR]` — start proxy with project bound
- `spillover stats <project>` — episode counts by type, top entities, top files, importance distribution, overflow timeline
- `spillover query <project> "..."` — ad-hoc retriever, show ranking + scores
- `spillover trace <request_id>` — replay pipeline for debugging
- `spillover pin <episode_id>` / `unpin` / `forget <episode_id>`

Optional bundled Grafana dashboard JSON in `dashboards/`.

## 10. Testing

Three tiers:

### 10.1 Unit (pytest, fast)

- adapter parse/build per provider with real wire-format fixtures
- retriever fusion: top-K vector + graph in → expected ordering out
- eviction selector: window state in → candidate list out
- facet parsers: regex decisions / code_refs / entities match fixtures
- decay function: score + age in → expected score out
- counter-compact usage rewrite: real usage in → visible usage out

### 10.2 Integration (pytest + httpx mock provider)

- end-to-end request: mock CLI → proxy → mock provider → response assertion
- overflow lifecycle: 100 forced turns, verify overflow fired, archived, facets ran
- retriever quality: 50-pair `(query, expected_episode_id)` dataset, assert recall@5 >= 90%
- counter-compact: mock CC simulates auto-compact trigger, assert usage rewrite prevents
- resilience: provider 5xx mid-stream, DB lock contention, embedder down

### 10.3 A/B benchmark (standalone script)

- 20 real coding tasks with vs without spillover
- measures: token usage, tasks completed without context loss, regression detection rate
- compare Claude Code vanilla vs Claude Code + spillover
- auto-generated markdown report

## 11. Performance targets (v1)

| Metric | Target |
|------|------:|
| Hot path overhead p50 | < 80ms |
| Hot path overhead p99 | < 200ms |
| Retriever query p99 | < 60ms |
| Archive durability | < 50ms |
| Facet pipeline throughput | >= 5 episodes/s single-worker |
| DB size per project per month (heavy use) | <= 100MB |

## 12. Acceptance gates for v1 release

- Counter-compact test: 0 `/compact` events in 100 forced turns on real Claude Code
- Retriever recall@5 >= 90% on 50-pair benchmark
- Zero data loss in chaos test (kill proxy mid-archive, recovers cleanly)
- A/B benchmark shows measurable gain on at least 2 metrics

## 13. Repo layout

```
spillover/
  pyproject.toml
  README.md
  src/spillover/
    __init__.py
    cli.py                 # `spillover` entry point
    proxy/
      app.py               # FastAPI app
      middleware.py        # project_id resolver
      streaming.py         # SSE pass-through
    adapters/
      base.py
      anthropic.py
      openai.py
    retriever/
      __init__.py
      vector.py
      graph.py
      fusion.py
      budget.py
    archive/
      writer.py
      schema_sqlite.sql
      schema_kuzu.cypher
    facet/
      worker.py
      embed.py
      ner.py
      decisions.py
      code_refs.py
      classifier.py
    decay/
      scheduler.py
    counter_compact/
      usage_rewrite.py
      env_vars.py
      detection.py
    wrappers/
      cc/                  # claude-code wrapper
      codex/
      cursor/
      generic/
    config.py
  tests/
    unit/
    integration/
    benchmark/
  dashboards/
    spillover.grafana.json
  docs/
    quickstart.md
    architecture.md
    counter-compaction.md
    superpowers/specs/2026-05-20-spillover-design.md   # this file
```

## 14. Non-goals (explicit)

- Not a fact memory ("user lives in Recife") — Mem0 covers that.
- Not a capability memory ("I have Playwright installed") — mneme covers that.
- Not a session summarizer — antithesis of the design.
- Not a context-window extender — we use the real window, but exploit it fully.
- Not multi-model orchestration — single provider per request, swap via env var.

## 15. Open questions (must answer before plan)

None at this stage. All structural decisions taken:

- name: spillover
- vector entry: transparent LLM proxy
- storage: sqlite-vec + Kuzu embedded
- scope: per-project (cwd-bound), `X-Project` header
- eviction: raw turn + async facet extraction
- stack: Python FastAPI + asyncio
- wire-protocol: pluggable adapters (Anthropic + OpenAI in v1)
- 4 memory types first-class
- importance decay with type-specific half-life
- counter-compaction defenses (4 vectors)
- hybrid retrieval via RRF
- payload layout: [system][LTM block][active][user]

## 16. References

- "Lost in the Middle" (Liu et al., TACL 2024) — attention degradation on long contexts
- "RAG-MCP" (Gan & Sun, 2025) — retrieval precision under load
- Anthropic Messages API docs — prompt caching, content blocks
- OpenAI Chat Completions API docs — wire format
- fastembed — quantized in-process embeddings
- sqlite-vec — vector extension for SQLite
- Kuzu — embedded property graph DB
- Reciprocal Rank Fusion (Cormack et al., SIGIR 2009)

---

End of spec.
