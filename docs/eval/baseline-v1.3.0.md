# spillover A/B benchmark

## summary

| metric | vanilla | spillover |
|---|---:|---:|
| tasks with all anchors hit | 14/15 | 0/15 |
| total input_tokens | 1508 | 2772 |
| total output_tokens | 2463 | 2610 |
| total errors | 0 | 0 |

## per-task

| task | mode | hits | misses | input | output | latency_ms |
|---|---|---|---|---:|---:|---:|
| db_choice | vanilla | SQLite,local | - | 88 | 22 | 1808 |
| db_choice | spillover | - | SQLite,local | 19 | 101 | 2489 |
| auth_bug | vanilla | middleware,42,jwt | - | 97 | 185 | 3478 |
| auth_bug | spillover | - | middleware,42,jwt | 126 | 104 | 2477 |
| adr_014 | vanilla | legacy,auth | - | 90 | 157 | 3067 |
| adr_014 | spillover | - | legacy,auth | 201 | 162 | 3320 |
| coolify | vanilla | letsencryptresolver,traefik | - | 128 | 200 | 2983 |
| coolify | spillover | traefik | letsencryptresolver | 199 | 200 | 3633 |
| erica_diabetes | vanilla | Basaglar,Fiasp | - | 115 | 200 | 3486 |
| erica_diabetes | spillover | - | Basaglar,Fiasp | 146 | 144 | 2735 |
| port_choice | vanilla | 8787,mneme | - | 91 | 92 | 2319 |
| port_choice | spillover | - | 8787,mneme | 128 | 145 | 3191 |
| watermark | vanilla | 0.85,1:1 | - | 91 | 188 | 3343 |
| watermark | spillover | - | 0.85,1:1 | 195 | 200 | 3121 |
| tokenizer_heuristic | vanilla | char/4,heuristic | - | 86 | 187 | 3510 |
| tokenizer_heuristic | spillover | - | char/4,heuristic | 255 | 200 | 3235 |
| rrf_weights | vanilla | priority,1.5 | - | 99 | 91 | 2542 |
| rrf_weights | spillover | - | priority,1.5 | 231 | 200 | 3428 |
| kuzu_schema | vanilla | Episode,MENTIONS | - | 106 | 200 | 3682 |
| kuzu_schema | spillover | - | Episode,MENTIONS | 204 | 200 | 3509 |
| decay | vanilla | exp,half | - | 108 | 200 | 3076 |
| decay | spillover | exp | half | 258 | 200 | 3466 |
| sse_rewrite | vanilla | usage | incremental | 104 | 200 | 3312 |
| sse_rewrite | spillover | usage | incremental | 210 | 200 | 4594 |
| profile_default | vanilla | coding,conversation | - | 110 | 200 | 3393 |
| profile_default | spillover | - | coding,conversation | 189 | 200 | 3418 |
| facet_queue | vanilla | 1024,queue | - | 88 | 200 | 3287 |
| facet_queue | spillover | - | 1024,queue | 203 | 189 | 3912 |
| counter_compact_vectors | vanilla | usage,intercept | - | 107 | 141 | 2569 |
| counter_compact_vectors | spillover | - | usage,intercept | 208 | 165 | 3318 |
