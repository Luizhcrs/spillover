# spillover A/B benchmark

## summary

| metric | vanilla | spillover |
|---|---:|---:|
| tasks with all anchors hit | 14/15 | 1/15 |
| total input_tokens | 1508 | 3160 |
| total output_tokens | 2389 | 2583 |
| total errors | 0 | 0 |

## per-task

| task | mode | hits | misses | input | output | latency_ms |
|---|---|---|---|---:|---:|---:|
| db_choice | vanilla | SQLite,local | - | 88 | 22 | 1721 |
| db_choice | spillover | - | SQLite,local | 19 | 139 | 2898 |
| auth_bug | vanilla | middleware,42,jwt | - | 97 | 103 | 2272 |
| auth_bug | spillover | - | middleware,42,jwt | 161 | 122 | 2450 |
| adr_014 | vanilla | legacy,auth | - | 90 | 200 | 3586 |
| adr_014 | spillover | - | legacy,auth | 233 | 187 | 3675 |
| coolify | vanilla | letsencryptresolver,traefik | - | 128 | 200 | 2914 |
| coolify | spillover | traefik | letsencryptresolver | 232 | 169 | 3204 |
| erica_diabetes | vanilla | Basaglar,Fiasp | - | 115 | 200 | 3499 |
| erica_diabetes | spillover | - | Basaglar,Fiasp | 174 | 161 | 3231 |
| port_choice | vanilla | 8787,mneme | - | 91 | 109 | 2437 |
| port_choice | spillover | - | 8787,mneme | 159 | 135 | 2770 |
| watermark | vanilla | 0.85,1:1 | - | 91 | 190 | 3298 |
| watermark | spillover | - | 0.85,1:1 | 227 | 200 | 3410 |
| tokenizer_heuristic | vanilla | char/4,heuristic | - | 86 | 169 | 3648 |
| tokenizer_heuristic | spillover | - | char/4,heuristic | 283 | 200 | 3437 |
| rrf_weights | vanilla | priority,1.5 | - | 99 | 65 | 2038 |
| rrf_weights | spillover | - | priority,1.5 | 242 | 143 | 3136 |
| kuzu_schema | vanilla | Episode,MENTIONS | - | 106 | 200 | 3300 |
| kuzu_schema | spillover | Episode | MENTIONS | 224 | 200 | 3740 |
| decay | vanilla | exp,half | - | 108 | 200 | 3589 |
| decay | spillover | exp | half | 227 | 200 | 4397 |
| sse_rewrite | vanilla | usage | incremental | 104 | 199 | 3777 |
| sse_rewrite | spillover | incremental,usage | - | 236 | 200 | 3793 |
| profile_default | vanilla | coding,conversation | - | 110 | 200 | 3310 |
| profile_default | spillover | - | coding,conversation | 294 | 200 | 3686 |
| facet_queue | vanilla | 1024,queue | - | 88 | 200 | 3054 |
| facet_queue | spillover | - | 1024,queue | 224 | 172 | 3214 |
| counter_compact_vectors | vanilla | usage,intercept | - | 107 | 132 | 2451 |
| counter_compact_vectors | spillover | - | usage,intercept | 225 | 155 | 3456 |
