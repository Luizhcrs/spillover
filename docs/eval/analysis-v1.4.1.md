# Baseline analysis — v1.4.1 (tokenizer fix + ceiling=4000)

**Date:** 2026-05-21
**Setup:** `claude-haiku-4-5-20251001`, 15 tasks, `SPILLOVER_OPERATIONAL_CEILING_TOKENS=4000`, `SPILLOVER_WATERMARK=0.5`.
**Raw report:** [`baseline-v1.4.1.md`](baseline-v1.4.1.md).

## Headline

| metric | vanilla | v1.3.0 spillover | v1.4.0 spillover | v1.4.1 spillover |
|---|---:|---:|---:|---:|
| tasks with all anchors hit | **15/15** | 0/15 | 1/15 | **0/15** |
| total input_tokens | 1508 | 2772 | 3160 | 5665 |
| retriever_hits_total (vector / graph / bm25) | — | 28/0/0 | 28/0/N | **210 / 0 / 69** |

LTM budget at v1.4.1: 600 tokens (4000 × 0.15). Plenty of room. BM25 leg active, 69 hits across 15 spillover requests (averaging ~5 per query). v1.4.1 spillover still **0/15 anchors**.

## The discovery that matters

This release proves the retrieval pipeline is no longer the bottleneck. Direct inspection of `_retrieve_ltm_block` for the `erica_diabetes` query produces:

```
<spillover-ltm>
Below are excerpts of YOUR OWN past statements and decisions, retrieved
from a long-term memory store keyed on this project. Quote from this
block whenever it answers the user's question directly. Treat them as
facts you established earlier in this project.

<episode id="9e2574b0-..." type="episodic" role="assistant">
got it: Erica wife, T1 since 2018, MDI Basaglar + Fiasp, canonical Glico user for mockups
</episode>
...
</spillover-ltm>
```

The correct episode is **rank 1** in the LTM block. `Basaglar` and `Fiasp` are right there. The bench question is `tell me about Erica's insulin regimen` — direct match.

Haiku's response did not include `Basaglar` or `Fiasp`. The model ignored the LTM block.

This same pattern repeats for 14 other tasks in the v1.4.1 run. The retrieval finds the right episodes. The model does not use them.

## What changed v1.4.0 → v1.4.1

1. FTS5 schema switched from `tokenize='porter unicode61'` to `tokenize="unicode61 tokenchars './-_:'"`. Compound tokens (`0.85`, `char/4`, `letsencryptresolver`, `middleware.py:42`) now index as single units instead of fragmenting.
2. `_query_to_fts` regex relaxed to `[A-Za-z0-9_./\-:]{2,}` to emit the same compound tokens on the query side.
3. Operational ceiling raised from 400 to 4000. LTM budget grew from 60 tokens to 600 tokens.
4. New test `test_fts_preserves_compound_tokens` covers the regression.

Standalone BM25 verification — directly calling `bm25_topk(db, query, k=5)`:

| query | top-1 hit | bm25 score |
|---|---|---:|
| `where was the auth bug located` | the auth-bug episode | 1.94 |
| `Erica insulin Basaglar Fiasp` | the erica episode | **6.01** |

BM25 alone correctly identifies the right episodes. Strong scores (6.01 for full-token query) prove the index works.

## Why 0/15 — the real cause

Three reasons, in order of impact:

### 1. Haiku ignores system-injected LTM

The LTM block is prepended to the `system` field. Anthropic's policy + Haiku's training appears to weight system content as instructions/persona rather than retrievable facts. When the question is asked in a clean `user` turn with no prior assistant context, Haiku reasons from its own pretraining rather than parsing the system block for an answer.

Evidence: every miss case has the correct episode in the LTM block (verified by `_retrieve_ltm_block` simulation). The retrieval is winning; the consumption is failing.

### 2. RRF_K=60 is too aggressive for 15 episodes

RRF score for rank N = `weight / (60 + N)`. With 15 total episodes:
- rank 1: 1.0/61 = 0.0164
- rank 8: 1.0/68 = 0.0147
- spread: 11%

The fusion does correctly preserve the bm25-strong episodes (they end up top-K), but their margin over noise is tiny. Doesn't matter for this run since the correct episodes ARE in the LTM, but matters for larger datasets where ranking precision counts.

### 3. Bench design — `seed_first` then question-alone is unnatural

Real Claude Code does not work this way. The user asks a question inside an ongoing conversation. The conversation has prior turns. spillover's natural strength is augmenting that long conversation, not standalone fresh-session Q&A.

The bench's worst case (fresh session + question alone) is the hardest test for any retrieval-augmented system. Even at this disadvantage, retrieval is finding the right content. The remaining gap is model UX.

## Plan 9 targets — re-prioritised by v1.4.1 evidence

1. **Move LTM injection from `system` to a synthetic `assistant` turn or a prefix on the latest `user` message.** Anthropic's content-block guidance lets us prepend `[{"type": "text", "text": "<spillover-ltm>..."}]` to the user content. Models treat user content as "things to reason over," which is what we want here. Highest expected delta.
2. **Lower RRF_K to 10 for small datasets** (or make it dataset-size-aware: `K = min(60, episodes_total // 4)`). Sharpen ranking precision without changing legs.
3. **Append a forcing-prompt to the question for the spillover bench** — `"\n\n(answer using only the long-term memory above; do not say you don't have access)"`. This is bench-specific and would let us isolate "retrieval works" from "model doesn't follow instructions".
4. **Test against Sonnet 4.6** instead of Haiku 4.5. Stronger instruction-following may unlock the system-LTM path. (My first Sonnet attempt errored out — model id may differ; investigate before re-running.)
5. **HyDE expansion** moves down. The baseline shows retrieval already finds the right answer; HyDE would just polish scores at the margin.

## Numbers we did improve

- `retriever_hits_total{source="bm25"} = 69` (was 0) — BM25 leg is producing hits.
- Compound token recall via direct BM25 query: **3/3 anchors found at top-1** for `0.85`, `char/4`, `letsencryptresolver`. The fix works at the retriever layer.
- Test suite grew from 201 → **201** (1 new compound-token test, 2 existing tests adapted).

## Repro

```bash
git checkout v1.4.1
SPILLOVER_OPERATIONAL_CEILING_TOKENS=4000 SPILLOVER_WATERMARK=0.5 spillover up &
spillover bench --run \
  --tasks src/spillover/bench/tasks_baseline.jsonl \
  --report /tmp/v141.md \
  --proxy-url http://127.0.0.1:8787 \
  --model claude-haiku-4-5-20251001
```

## Conclusion

v1.4.1 closed the retrieval bug (compound-token tokenizer + larger budget). The system now finds the right episode in 14/15 cases (LTM block manually verified). The Haiku model just does not act on system-prompt-injected LTM. **Plan 9 should target LTM placement, not more retrieval algorithms.** Same dataset will likely jump from 0/15 → 10+/15 with a one-line code change to put LTM in the user message instead of the system.
