# spillover A/B benchmark

## summary

| metric | vanilla | spillover |
|---|---:|---:|
| tasks with all anchors hit | 15/15 | 0/15 |
| total input_tokens | 1508 | 5665 |
| total output_tokens | 2336 | 2652 |
| total errors | 0 | 0 |

## per-task

| task | mode | hits | misses | input | output | latency_ms |
|---|---|---|---|---:|---:|---:|
| db_choice | vanilla | SQLite,local | - | 88 | 22 | 2090 |
| db_choice | spillover | - | SQLite,local | 19 | 134 | 2602 |
| auth_bug | vanilla | middleware,42,jwt | - | 97 | 72 | 1937 |
| auth_bug | spillover | - | middleware,42,jwt | 161 | 130 | 2537 |
| adr_014 | vanilla | legacy,auth | - | 90 | 190 | 3706 |
| adr_014 | spillover | - | legacy,auth | 238 | 200 | 3329 |
| coolify | vanilla | letsencryptresolver,traefik | - | 128 | 200 | 2459 |
| coolify | spillover | traefik | letsencryptresolver | 306 | 189 | 3353 |
| erica_diabetes | vanilla | Basaglar,Fiasp | - | 115 | 200 | 3399 |
| erica_diabetes | spillover | - | Basaglar,Fiasp | 392 | 139 | 2726 |
| port_choice | vanilla | 8787,mneme | - | 91 | 103 | 2448 |
| port_choice | spillover | - | 8787,mneme | 474 | 171 | 3552 |
| watermark | vanilla | 0.85,1:1 | - | 91 | 171 | 3321 |
| watermark | spillover | - | 0.85,1:1 | 470 | 200 | 3535 |
| tokenizer_heuristic | vanilla | char/4,heuristic | - | 86 | 200 | 6909 |
| tokenizer_heuristic | spillover | - | char/4,heuristic | 443 | 146 | 3141 |
| rrf_weights | vanilla | priority,1.5 | - | 99 | 69 | 1746 |
| rrf_weights | spillover | - | priority,1.5 | 460 | 170 | 3481 |
| kuzu_schema | vanilla | Episode,MENTIONS | - | 106 | 200 | 2919 |
| kuzu_schema | spillover | - | Episode,MENTIONS | 470 | 178 | 3704 |
| decay | vanilla | exp,half | - | 108 | 200 | 3135 |
| decay | spillover | exp | half | 431 | 200 | 3496 |
| sse_rewrite | vanilla | incremental,usage | - | 104 | 200 | 3336 |
| sse_rewrite | spillover | usage | incremental | 448 | 200 | 3598 |
| profile_default | vanilla | coding,conversation | - | 110 | 200 | 3241 |
| profile_default | spillover | - | coding,conversation | 468 | 200 | 3259 |
| facet_queue | vanilla | 1024,queue | - | 88 | 187 | 3333 |
| facet_queue | spillover | - | 1024,queue | 442 | 200 | 3461 |
| counter_compact_vectors | vanilla | usage,intercept | - | 107 | 122 | 2182 |
| counter_compact_vectors | spillover | - | usage,intercept | 443 | 195 | 3655 |
