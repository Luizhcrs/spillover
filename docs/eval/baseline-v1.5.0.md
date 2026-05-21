# spillover A/B benchmark

## summary

| metric | vanilla | spillover |
|---|---:|---:|
| tasks with all anchors hit | 14/15 | 0/15 |
| total input_tokens | 1393 | 5623 |
| total output_tokens | 2142 | 2702 |
| total errors | 1 | 0 |

## per-task

| task | mode | hits | misses | input | output | latency_ms |
|---|---|---|---|---:|---:|---:|
| db_choice | vanilla | SQLite,local | - | 88 | 22 | 2250 |
| db_choice | spillover | - | SQLite,local | 19 | 125 | 2541 |
| auth_bug | vanilla | middleware,42,jwt | - | 97 | 76 | 1885 |
| auth_bug | spillover | - | middleware,42,jwt | 160 | 142 | 2780 |
| adr_014 | vanilla | legacy,auth | - | 90 | 169 | 2992 |
| adr_014 | spillover | - | legacy,auth | 235 | 168 | 3502 |
| coolify | vanilla | letsencryptresolver,traefik | - | 128 | 200 | 3756 |
| coolify | spillover | traefik | letsencryptresolver | 303 | 186 | 4282 |
| erica_diabetes | vanilla | - | - | 0 | 0 | 20207 |
| erica_diabetes | spillover | - | Basaglar,Fiasp | 389 | 151 | 3465 |
| port_choice | vanilla | 8787,mneme | - | 91 | 112 | 2695 |
| port_choice | spillover | - | 8787,mneme | 468 | 185 | 4017 |
| watermark | vanilla | 0.85,1:1 | - | 91 | 131 | 2688 |
| watermark | spillover | - | 0.85,1:1 | 468 | 200 | 3353 |
| tokenizer_heuristic | vanilla | char/4,heuristic | - | 86 | 200 | 3765 |
| tokenizer_heuristic | spillover | - | char/4,heuristic | 445 | 200 | 3887 |
| rrf_weights | vanilla | priority,1.5 | - | 99 | 98 | 2158 |
| rrf_weights | spillover | - | priority,1.5 | 457 | 191 | 3501 |
| kuzu_schema | vanilla | Episode,MENTIONS | - | 106 | 200 | 3129 |
| kuzu_schema | spillover | - | Episode,MENTIONS | 464 | 185 | 3506 |
| decay | vanilla | exp,half | - | 108 | 200 | 3301 |
| decay | spillover | half | exp | 426 | 200 | 3268 |
| sse_rewrite | vanilla | usage | incremental | 104 | 199 | 3833 |
| sse_rewrite | spillover | usage | incremental | 448 | 200 | 3332 |
| profile_default | vanilla | coding,conversation | - | 110 | 200 | 3045 |
| profile_default | spillover | - | coding,conversation | 463 | 200 | 3656 |
| facet_queue | vanilla | 1024,queue | - | 88 | 200 | 3434 |
| facet_queue | spillover | - | 1024,queue | 436 | 200 | 3136 |
| counter_compact_vectors | vanilla | usage,intercept | - | 107 | 135 | 2702 |
| counter_compact_vectors | spillover | - | usage,intercept | 442 | 169 | 3160 |
