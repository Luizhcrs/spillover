# Long-conversation bench

## summary

| metric | vanilla_truncated | spillover |
|---|---:|---:|
| scenarios w/ all anchors hit | 0/2 | 2/2 |
| total input_tokens | 347 | 4505 |
| total output_tokens | 203 | 159 |
| total errors | 0 | 0 |

## per-scenario

| scenario | mode | hits | misses | input | output | latency_ms |
|---|---|---|---|---:|---:|---:|
| db_choice_long | vanilla_truncated | - | SQLite,local | 202 | 105 | 2766 |
| db_choice_long | spillover | SQLite,local | - | 2514 | 48 | 2771 |
| auth_bug_long | vanilla_truncated | - | middleware,42,jwt | 145 | 98 | 2337 |
| auth_bug_long | spillover | middleware,42,jwt | - | 1991 | 111 | 2840 |
