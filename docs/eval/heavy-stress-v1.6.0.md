# Heavy stress bench (200 turns, ~100k chars)

## summary

| metric | vanilla_truncated | spillover |
|---|---:|---:|
| anchors-hit (out of 4) | 0/4 | 3/4 |
| turns sent | 13 | 401 |
| chars sent | 2709 | 81165 |
| visible input_tokens | 767 | 22320 |
| spillover_real_input_tokens | - | 22541 |
| output_tokens | 298 | 147 |
| latency_ms | 6752 | 4401 |
| errors | 0 | 0 |

## anchors checked

- `SQLite` (anchored at turn turn05_db)
- `middleware.py:42` (anchored at turn turn50_bug)
- `8787` (anchored at turn turn100_port)
- `exp(-age/half_life)` (anchored at turn turn150_decay)

## responses

### vanilla_truncated

```
I need to be honest: I cannot reliably recall those four specific facts from our conversation history.

Looking back at what we've discussed, I see:
- **Database**: Kuzu is mentioned (iteration 197-198) as a graph database for the schema, but I don't have context on *why* it was chosen over alternatives, or if there's a primary SQL database I'm missing
- **Auth bug**: You mentioned checking "imports for circular dependencies in the auth module" (iteration 195) and it came back clean, but I have no record of *where* a bug actually was or what it involved
- **Spillover port**: I don't see any mention of a port number or spillover service in our conversation
- **Importance decay formula**: I see references to type-weights (1.5, 1.2, 1.4, 1.0, 1.0) and RRF scoring in iteration 197, but no explicit decay formula

Rather than guess and risk giving you false citations, I should ask: **Can you point me to which earlier conversation or context these details come from?** They may be from a prior session I don't have access to, or embedded in code you're working from that I haven't seen directly.

What would be most useful—should I ask clarifying questions, or do you want to provide those specs?
```

### spillover

```
**Quick recap:**

(1) **Database choice:** SQLite over Postgres because "deployment is local-only zero-infra few-hundred-GB max"

(2) **Auth bug location:** middleware.py line 42 — "jwt expiry comparison uses < instead of <="

(3) **Spillover proxy port:** 8787 — "chosen because mneme is 7777 and we wanted visually distinct close numbers"

(4) **Importance decay formula:** base × exp(-age/half_life) + min(hit_count×0.05, 0.5) — "exponential half-life by type"
```

