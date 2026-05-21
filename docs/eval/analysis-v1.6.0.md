# Eval analysis v1.6.0

Date: 2026-05-21
Branch: feat/plan10-vision-complete
Model: claude-haiku-4-5-20251001
Ceiling: 6000 tokens, watermark 0.7, placement=between

---

## Long-conversation bench

Source: long-conversation-v1.6.0.md

| metric | vanilla_truncated | spillover | delta |
|---|---:|---:|---:|
| scenarios w/ all anchors hit | 0/2 | 2/2 | +2 |
| total input_tokens | 347 | 4505 | +4158 |
| total output_tokens | 203 | 159 | -44 |
| errors | 0 | 0 | 0 |

db_choice_long: vanilla missed both anchors (SQLite, local). spillover hit both.
The decision was embedded at turn ~5 of 80+. With keep_last_n=8, anchor gone.

auth_bug_long: vanilla missed all three anchors (middleware, 42, jwt). spillover hit all.
Bug location (middleware.py:42, jwt off-by-one) established at start of 80+ turn conv.

spillover 2/2 vs vanilla_truncated 0/2.

---

## Landing-page logic retention

Source: landing-page-logic-v1.6.0.md

| mode | details preserved |
|---|---:|
| vanilla_truncated | 0/9 |
| spillover | 9/9 |

All 9 named details recovered by spillover. Vanilla lost every one (decisions in
first 18 turns of 48-turn conversation, last 8 turns kept nothing relevant).

Details hit by spillover: primary_cta, accent_hex (#06FFB0), body_font (Inter),
heading_font (Geist Mono), hero_headline, section_count (5), pricing_cta
(Get the proxy), email_placeholder (work@yourcompany.com), footer_year (2026).

---

## Cost

bench-long: ~$0.005 (Haiku, ~5k input total)
bench-logic: ~$0.03 (Haiku, 18 calls x ~500 input tokens each)

---

## Plan 11 candidates

1. Anchor precision via causal multi-hop: add bench where anchor fact references a
   decision from a different thread, requiring 2+ causal hops.
2. Partial recall bench: current benches are binary (all or none). Need a scenario
   where some details are harder to retrieve.
3. Token budget tuning: LTM block at ceiling=6000 costs 4505 tokens for 2 scenarios.
   Tune ltm_budget_pct per-profile.
4. Task type recall bench: exercise the new task memory_type with TODO items.
5. Placement A/B: compare turns vs between on same scenarios.
