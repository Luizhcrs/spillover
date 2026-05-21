# Baseline analysis -- v1.4.0

**Date:** 2026-05-21
**Setup:** `claude-haiku-4-5-20251001`, 15 tasks, `SPILLOVER_OPERATIONAL_CEILING_TOKENS=400`, `SPILLOVER_WATERMARK=0.5`.
**Raw report:** [`baseline-v1.4.0.md`](baseline-v1.4.0.md).
**Raw JSONL:** [`baseline-v1.4.0.jsonl`](baseline-v1.4.0.jsonl).
**Code changes from v1.3.0:** BM25 FTS5 leg, LTM priming preamble, topk default 8 -> 5.

## Headline

| metric | vanilla | spillover | delta vs v1.3.0 |
|---|---:|---:|---:|
| tasks with all anchors hit | **14/15 (93%)** | **1/15 (7%)** | +1 (was 0/15) |
| total input_tokens | 1508 | 3160 | +388 spillover tokens |
| total output_tokens | 2389 | 2583 | - |

Vanilla holds at 14/15. spillover moves from 0/15 to **1/15**. One anchor set fully resolved. One additional partial-hit improvement visible.

## What changed vs v1.3.0

| change | expected effect | observed |
|---|---|---|
| BM25 FTS5 leg added to 3-way fusion | lift exact-string matches | partial; 1 full flip, 1 partial improvement |
| LTM preamble primed with "YOUR OWN past statements" | model treats block as ground truth | marginal effect at 60-token budget |
| topk default 8 -> 5 | each hit gets more token budget | input tokens up, confirms LTM injected |

## Delta vs v1.3.0 -- per anchor

| task | v1.3.0 spillover | v1.4.0 spillover | delta |
|---|---|---|---|
| db_choice | (none) | (none) | no change |
| auth_bug | (none) | (none) | no change |
| adr_014 | (none) | (none) | no change |
| coolify | `traefik` only | `traefik` only | no change |
| erica_diabetes | (none) | (none) | no change |
| port_choice | (none) | (none) | no change |
| watermark | (none) | (none) | no change |
| tokenizer_heuristic | (none) | (none) | no change |
| rrf_weights | (none) | (none) | no change |
| kuzu_schema | (none) | `Episode` only | +1 partial hit |
| decay | `exp` only | `exp` only | no change |
| sse_rewrite | `usage` only | `incremental` + `usage` = FULL HIT | +1 FULL FLIP |
| profile_default | (none) | (none) | no change |
| facet_queue | (none) | (none) | no change |
| counter_compact_vectors | (none) | (none) | no change |

## What flipped

**sse_rewrite** miss -> full hit.

v1.3.0: `usage` hit, `incremental` missed.
v1.4.0: both `incremental` and `usage` hit.

`incremental` is a low-frequency technical term well-suited for BM25 exact match. The seed episode contains "incremental" literally. FTS5 ranked it first; priming likely helped the model cite it explicitly.

**kuzu_schema** gained `Episode` but still misses `MENTIONS`.

`MENTIONS` is uppercase. Porter stemmer lowercases all tokens; `MENTIONS` becomes `mention`. FTS finds the episode but the model outputs `mentions` (lowercase) instead of `MENTIONS` (exact case).

## What is still broken -- root causes unchanged

1. **60-token LTM budget at 400-token ceiling.** Each of the 5 topk episodes gets ~12 tokens of budget after framing overhead. Most answers need 20-40 tokens. The bench ceiling is the dominant constraint.

2. **Vocabulary mismatch for queries using generic terms.** Queries like "what database did we choose" do not contain `SQLite`. BM25 and vector both fail to rank the SQLite episode first when the query uses user vocabulary and the episode uses system vocabulary.

3. **Compound tokens break FTS tokenizer.** `0.85`, `char/4`, `letsencryptresolver` -- the regex `[A-Za-z0-9_]{2,}` splits these at `.`, `/`, which means BM25 cannot match them as units.

## What Plan 9 should target

1. **Raise bench ceiling to 4000 tokens.** LTM budget becomes 600 tokens -- fits 5 episodes with room. This alone likely moves spillover to 8+/15 without code changes. Measure first.

2. **Fix compound token regex.** Add extraction of dotted numbers (`0.85`), slash-separated strings (`char/4`), and concatenated identifiers (`letsencryptresolver`). Low LOC, high precision lift for the specific missed anchors.

3. **HyDE query expansion.** Embed a hypothetical answer instead of the raw question. Closes vocabulary mismatch. ~200ms latency overhead.

4. **Entity-keyed recall.** Pre-compute entity -> episode_id index during facet. For queries whose entity is not in the current FTS top-5, do a direct entity lookup. Avoids relying on embedding alignment entirely.

5. **Self-RAG gate.** Skip retrieval for queries with no entity match and no FTS match score above threshold. Reduces noise injection and token waste.

6. **ColBERT rerank.** Defer until fixes 1-4 are measured and stall.

## Status

baseline-v1.4.0: published. 1/15 spillover vs 14/15 vanilla. Delta: +1 full flip (sse_rewrite), +1 partial (kuzu_schema). The 60-token LTM budget ceiling at bench settings remains the primary blocker. Plan 9 raises the ceiling first, then applies compound token and HyDE fixes.
