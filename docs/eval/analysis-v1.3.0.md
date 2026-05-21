# Baseline analysis — v1.3.0

**Date:** 2026-05-21
**Setup:** `claude-haiku-4-5-20251001`, 15 tasks, `SPILLOVER_OPERATIONAL_CEILING_TOKENS=400`, `SPILLOVER_WATERMARK=0.5`.
**Raw report:** [`baseline-v1.3.0.md`](baseline-v1.3.0.md).
**Raw JSONL:** [`baseline-v1.3.0.jsonl`](baseline-v1.3.0.jsonl).

## Headline

| metric | vanilla | spillover |
|---|---:|---:|
| tasks with all anchors hit | **14/15 (93%)** | **0/15 (0%)** |
| total input_tokens | 1508 | 2772 |
| total output_tokens | 2463 | 2610 |

Vanilla (history sent inline) hits 14/15. **spillover (history must be recalled via LTM) hits 0/15.**

This is a public baseline. It is not the final number. It is the starting point that Plan 8 has to beat.

## What this proves

1. The end-to-end harness works against real Anthropic. OAuth flowed, requests routed, responses parsed, anchor-counting logic correct (proven by vanilla 14/15).
2. spillover *did archive* — `episodes: 15, evicted: 15, embedded: 15`. Facet pipeline ran on every turn.
3. spillover *did retrieve* — Prometheus shows `retriever_hits_total{source="hybrid"} = 28.0` across 15 spillover requests.
4. spillover *did inject* — spillover input tokens (avg 185) are larger than vanilla (avg 100), proving an LTM block was prepended.

The retrieval pipeline is structurally working. The CONTENT it injects does not change the model's answer.

## Why anchors miss — three root causes

### 1. RRF scores are nearly flat

Ad-hoc query on the populated DB:

```
$ spillover query <pid> "where was the auth bug located"
47a603e8  score=0.0164  type=episodic  source=fusion   ← ADR-014 (about legacy auth, not the bug)
fef50e89  score=0.0161  type=episodic  source=fusion   ← THE auth bug episode (right answer, rank 2)
aef2682b  score=0.0159  type=episodic  source=fusion
035fa645  score=0.0156  type=episodic  source=fusion
8eb1605b  score=0.0154  type=episodic  source=fusion   ← Erica (irrelevant)
0801f7ba  score=0.0152  type=episodic  source=fusion   ← Coolify (irrelevant)
d24cbb31  score=0.0149  type=episodic  source=fusion
c1c384a5  score=0.0147  type=episodic  source=fusion
```

Spread: 0.0147 → 0.0164 = **11% gap between #1 and #8.** Effectively random ordering. The relevant episode is in the top-K but is not differentiated from noise.

Cause: the embedder is `nomic-embed-text-v1.5-Q` — a generic English embedder. Short episodes (15-50 tokens of natural language) live in a tight cosine cluster; the embeddings cannot distinguish "auth bug at middleware.py:42" from "ADR-014 about legacy auth middleware" because they share the lexical surface "auth middleware".

### 2. LTM budget is too tight at low ceiling

```
ltm_tokens = operational_ceiling × ltm_pct = 400 × 0.15 = 60 tokens
```

60 tokens fits ~1-2 short episodes after `<spillover-ltm>` framing overhead. Even when the correct episode is retrieved at rank 2, `trim_to_budget` drops it.

At a realistic ceiling (200k+), LTM budget is 30k+ tokens — generous. The bench used 400 tokens to *force* eviction within short conversations. The budget squeezed out the win.

### 3. Question-only request lacks priming

The bench sends `[{"role":"user","content":"<question>"}]` — one message, no history, no system prompt nudging "use the LTM block". The model receives:

```
[SYSTEM: <spillover-ltm>...</spillover-ltm>]
[USER: where was the auth bug located]
```

With no priming, the model treats the LTM block as background noise unless the prior episodes contain the *exact* string the question asks about. The right episode might be in the block but the model does not extract it.

## Targeted fixes — Plan 8 priorities, re-ordered by baseline evidence

The original Plan 8 list was HyDE → BM25 → Self-RAG gate → ColBERT. The baseline shifts the order:

1. **BM25 lexical leg first.** All 5 anchor-miss cases below have exact strings in the seed episodes: `SQLite`, `middleware`, `42`, `jwt`, `Basaglar`, `Fiasp`, `8787`, `letsencryptresolver`, `Episode`, `MENTIONS`, `0.85`, `char/4`. BM25 finds these by literal match — no embedding needed. Highest signal-per-LOC fix.
2. **System-prompt priming for spillover route.** Prepend a one-line instruction to LTM: `"The block below contains your own past statements; quote from it when relevant."` Zero new infra. Test before any model swap.
3. **HyDE query expansion.** Embed a hypothetical answer instead of the bare question. Should compress the score spread by elevating semantically-aligned episodes. ~200ms latency overhead.
4. **Top-K vs LTM budget rebalance.** Cap top-K at 3-5 for short queries; give each more token room. Currently top-K=8 splits the budget thin.
5. **Self-RAG gate.** Skip retrieval entirely for queries that don't reference project content (saves ~500 tokens of injected noise per casual turn).
6. **ColBERT rerank.** Defer until 1-3 are measured. If post-fix spread is still <50%, ColBERT becomes the lever.

## Per-anchor failure pattern

| task | spillover hits | what missed |
|---|---|---|
| db_choice | (none) | `SQLite`, `local` |
| auth_bug | (none) | `middleware`, `42`, `jwt` |
| adr_014 | (none) | `legacy`, `auth` |
| coolify | `traefik` | `letsencryptresolver` |
| erica_diabetes | (none) | `Basaglar`, `Fiasp` |
| port_choice | (none) | `8787`, `mneme` |
| watermark | (none) | `0.85`, `1:1` |
| tokenizer_heuristic | (none) | `char/4`, `heuristic` |
| rrf_weights | (none) | `priority`, `1.5` |
| kuzu_schema | (none) | `Episode`, `MENTIONS` |
| decay | `exp` | `half` |
| sse_rewrite | `usage` | `incremental` |
| profile_default | (none) | `coding`, `conversation` |
| facet_queue | (none) | `1024`, `queue` |
| counter_compact_vectors | (none) | `usage`, `intercept` |

`exp` and `usage` are common English tokens that the model produced from prior knowledge of attention papers / API spec — not from LTM retrieval. The TRUE recall is 0/15, not 2/15.

## Repro

```bash
git checkout v1.3.0
pip install -e ".[dev]"

# Terminal 1
SPILLOVER_OPERATIONAL_CEILING_TOKENS=400 SPILLOVER_WATERMARK=0.5 spillover up

# Terminal 2
spillover bench --run \
  --tasks src/spillover/bench/tasks_baseline.jsonl \
  --report /tmp/repro.md \
  --proxy-url http://127.0.0.1:8787 \
  --model claude-haiku-4-5-20251001
```

Numbers are deterministic up to model nondeterminism (~±10% per anchor).

## What this baseline does NOT prove

- It does not represent the user-facing case where ceiling is 200k+. Bench compressed the budget to force eviction in short conversations; that distorts LTM headroom.
- It does not test the **counter-compaction** path. The bench question-call lacks history; rescue logic never fires because there is no "missing turn" to diff against. Counter-compaction is tested in `tests/integration/test_counter_compact_lifecycle.py` against synthetic two-request flows.
- It does not measure latency under realistic load. All 30 calls were sequential against Haiku; the proxy never saw concurrent traffic.

## Status

baseline-v1.3.0: published, honest, used as the comparison floor for Plan 8.
