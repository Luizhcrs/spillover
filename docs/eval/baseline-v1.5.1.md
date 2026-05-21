# spillover A/B benchmark

## summary

| metric | vanilla | spillover |
|---|---:|---:|
| tasks with all anchors hit | 14/15 | 1/15 |
| total input_tokens | 1508 | 5681 |
| total output_tokens | 2445 | 2796 |
| total errors | 0 | 0 |

## per-task

| task | mode | hits | misses | input | output | latency_ms |
|---|---|---|---|---:|---:|---:|
| db_choice | vanilla | SQLite,local | - | 88 | 22 | 1905 |
| db_choice | spillover | - | SQLite,local | 19 | 143 | 3887 |
| auth_bug | vanilla | middleware,42,jwt | - | 97 | 67 | 2394 |
| auth_bug | spillover | - | middleware,42,jwt | 159 | 161 | 3594 |
| adr_014 | vanilla | legacy,auth | - | 90 | 200 | 3951 |
| adr_014 | spillover | - | legacy,auth | 234 | 166 | 3417 |
| coolify | vanilla | letsencryptresolver,traefik | - | 128 | 200 | 3134 |
| coolify | spillover | traefik | letsencryptresolver | 306 | 184 | 3278 |
| erica_diabetes | vanilla | Basaglar,Fiasp | - | 115 | 200 | 3461 |
| erica_diabetes | spillover | - | Basaglar,Fiasp | 392 | 163 | 3084 |
| port_choice | vanilla | 8787,mneme | - | 91 | 115 | 2590 |
| port_choice | spillover | - | 8787,mneme | 474 | 183 | 3330 |
| watermark | vanilla | 0.85,1:1 | - | 91 | 198 | 3304 |
| watermark | spillover | - | 0.85,1:1 | 471 | 200 | 3469 |
| tokenizer_heuristic | vanilla | char/4,heuristic | - | 86 | 200 | 3847 |
| tokenizer_heuristic | spillover | - | char/4,heuristic | 447 | 200 | 3728 |
| rrf_weights | vanilla | priority,1.5 | - | 99 | 107 | 2330 |
| rrf_weights | spillover | - | priority,1.5 | 461 | 196 | 3408 |
| kuzu_schema | vanilla | Episode,MENTIONS | - | 106 | 200 | 3334 |
| kuzu_schema | spillover | Episode | MENTIONS | 476 | 200 | 3584 |
| decay | vanilla | exp,half | - | 108 | 200 | 3068 |
| decay | spillover | exp | half | 437 | 200 | 3733 |
| sse_rewrite | vanilla | usage | incremental | 104 | 200 | 3521 |
| sse_rewrite | spillover | incremental,usage | - | 453 | 200 | 3900 |
| profile_default | vanilla | coding,conversation | - | 110 | 200 | 3476 |
| profile_default | spillover | - | coding,conversation | 462 | 200 | 3937 |
| facet_queue | vanilla | 1024,queue | - | 88 | 200 | 3280 |
| facet_queue | spillover | - | 1024,queue | 443 | 200 | 4193 |
| counter_compact_vectors | vanilla | usage,intercept | - | 107 | 136 | 2610 |
| counter_compact_vectors | spillover | - | usage,intercept | 447 | 200 | 3834 |
