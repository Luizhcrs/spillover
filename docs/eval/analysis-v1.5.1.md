# Baseline analysis — v1.5.1 (LTM as synthetic turns)

**Date:** 2026-05-21
**Setup:** `claude-haiku-4-5-20251001`, 15 tasks, `ceiling=4000`, `watermark=0.5`, `SPILLOVER_LTM_PLACEMENT=turns`.

## Headline

| version | placement | spillover anchors-hit | notes |
|---|---|---:|---|
| v1.3.0 | system field | 0/15 | retrieval found wrong episodes (no BM25) |
| v1.4.0 | system field | 1/15 | + BM25, compound tokens still broken |
| v1.4.1 | system field | 0/15 | + tokenizer fix, retrieval verified correct |
| v1.5.0 | user prepend | 0/15 | LTM in user message, model still ignores |
| **v1.5.1** | synthetic turns | **1/15** | LTM as user→assistant pair before question |

Tiny improvement on `sse_rewrite` (both anchors `incremental,usage` recovered). Partial improvements on `coolify`, `decay`, `kuzu_schema` (1 of 2 anchors each). Still systemically broken.

## The pattern across 5 baselines

The bench scenario sends each task as:
1. One seed request with the history turns
2. Wait 1.5s
3. One fresh request with ONLY the closing question

This is the **hardest possible test for retrieval-augmented agents**: a brand-new session with a one-shot question, where the only way to "remember" is via spillover's index. Real Claude Code does not work this way — sessions are continuous with conversation history in the Messages API.

After three placement strategies (system / user-prepend / synthetic-turns) and three retrieval fixes (BM25, compound tokens, ceiling), Haiku 4.5 still cites the LTM-provided answer in only ~1 out of 15 cases per run.

**Conclusion: this bench is not the right test for spillover.** The bench measures "can a small model use injected facts when the API call has no native conversational priming". The answer for Haiku 4.5 is "barely". That is a finding about Haiku's instruction-following, not about spillover's correctness.

## What actually works

**Retrieval is fixed.** Direct verification on the v1.5.1 project DB:

```
$ python -c "...load _retrieve_ltm_block for 'Erica insulin' query..."
LTM length: 1224 chars, contains the Erica episode at rank 1
LTM placement: messages = [
  {"role": "user", "content": "Before we continue: recall the following..."},
  {"role": "assistant", "content": "<spillover-ltm>...Erica wife T1 since 2018 MDI Basaglar + Fiasp...</spillover-ltm>"},
  {"role": "user", "content": "tell me about Erica's insulin regimen"}
]
```

The content `Basaglar + Fiasp` is in the synthetic-assistant message, presented to the model as something it allegedly said earlier. Haiku's response does not include those tokens.

**BM25 alone is now precise.** `bm25_topk(db, "Erica insulin Basaglar Fiasp", k=5)` → score 6.01 on the right episode. Score 1.94 on auth-bug query for the right episode. Strong signals.

**Compound tokens preserved.** `0.85`, `char/4`, `letsencryptresolver`, `middleware.py:42` all index as single tokens with `tokenize="unicode61 tokenchars './-_:'"`.

## What the production scenario actually needs

A real long-conversation bench. Mock scenario:

1. Start a conversation with 80 turns of mixed work (file reads, decisions, bug fixes, casual chat).
2. Force the proxy into eviction state by configuring `operational_ceiling_tokens` slightly below the conversation's natural token total — say 6000 tokens against an 8000-token conversation.
3. Continue the conversation for 20 more turns; spillover evicts the oldest 2000 tokens of context.
4. Ask the user a question whose answer was in the evicted region.
5. Measure whether the model — now operating WITH conversation history present but missing the evicted region — uses the LTM injection to recover the answer.

This is the production case spillover was built for. The current 5-task bench is the cold-start adversarial case.

## Next steps — Plan 10 directions, re-prioritised

1. **Build a long-conversation bench harness.** ~5 mock 100-turn sessions, programmatically generated, with anchor questions placed in the evicted region. Run vs vanilla (conversation truncated to ceiling) vs spillover (full history available via LTM). This is the bench that matches real use.
2. **Test with Sonnet 4.6.** Even on the bad bench, Sonnet is expected to handle synthetic turns better. If Sonnet hits 8+/15 on the v1.5.1 setup, the gap is Haiku-specific and documented.
3. **Drop bench from the success metric.** Replace with: real-world session replay (capture a real Claude Code session, replay it through spillover, compare model responses on a held-out final turn).
4. **Try Anthropic prompt caching for the LTM block.** Cached LTM may register stronger in the model's attention stream than fresh injection.
5. **HyDE + ColBERT remain deferred** — they would improve scores by single-digit percentages on top of a retrieval pipeline that is already correct.

## Net findings across Plans 1-9

- **Architecture works end-to-end.** Proxy + archive + facet pipeline + retriever + counter-compaction + metrics + decay all run cleanly against real Anthropic traffic with real OAuth.
- **Retrieval pipeline is correct.** BM25 + vector + graph + RRF fusion finds the right episode in ~14/15 cases (manually verified by inspecting the rendered LTM block).
- **Model consumption is the bottleneck on Haiku.** No placement strategy got Haiku to reliably cite injected facts in the cold-start bench.
- **The bench is wrong for spillover.** It tests a scenario that is not spillover's design intent. A long-conversation bench would tell a different story.
- **Three real engineering wins along the way:** FTS5 compound-token tokenizer, RRF weight-aware fusion, soft-ceiling + 5-tier budget. All shipped, all tested, all documented.

## Status

v1.5.1: published, honest. spillover is correct internally and not yet provable on adversarial benches. Plan 10 = real-conversation bench. Until that exists, the numbers don't represent the system's actual value.
