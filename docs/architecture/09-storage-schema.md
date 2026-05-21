# 09 — Storage schema (per-project)

Each project gets its own SQLite + Kuzu pair under `~/.spillover/projects/<sha1(cwd)>/`.

```mermaid
erDiagram
    episodes ||--o| vec_episodes : "1:1 (after facet)"
    episodes ||--o| episodes_fts : "1:1 (FTS5 mirror)"
    Episode_kuzu ||--o{ Entity : "MENTIONS"
    Episode_kuzu ||--o{ File : "TOUCHED"
    Episode_kuzu ||--o{ Decision : "IMPLEMENTS"
    Episode_kuzu ||--o{ Command : "RAN"
    Episode_kuzu ||--o{ Episode_kuzu : "AFTER"

    episodes {
        TEXT id PK
        TEXT project_id
        TEXT role
        TEXT content_json
        TEXT tool_calls_json
        TEXT code_refs_json
        INT token_count
        INT ts
        TEXT hash UK
        INT evicted "0|1 CHECK"
        INT pinned "0|1 CHECK"
        INT hit_count
        TEXT memory_type "priority|procedural|semantic|episodic|task"
        INT facet_pending "0|1 CHECK"
        INT compaction_rescued "0|1 CHECK"
    }

    vec_episodes {
        TEXT episode_id PK_FK
        FLOAT768 embedding "sqlite-vec virtual"
        TEXT memory_type
        REAL importance
        INT ts
    }

    episodes_fts {
        TEXT episode_id "UNINDEXED"
        TEXT body "FTS5 tokenize=unicode61 tokenchars ./-_:"
    }

    seen_turns {
        TEXT project_id PK
        TEXT turn_hash PK
        INT turn_index
        TEXT content_json
        INT first_seen_ts
        INT last_seen_ts
    }

    Episode_kuzu {
        STRING id PK
        INT64 ts
        STRING memory_type
        DOUBLE importance
    }

    Entity {
        STRING name PK
        STRING kind "file|url|identifier|command"
    }

    File {
        STRING path PK
        STRING ext
    }

    Decision {
        STRING hash PK
        STRING summary
    }

    Command {
        STRING sig PK
        INT64 first_seen_ts
    }
```

## SQLite tables

### `episodes`

Source of truth for archived turn content. Raw, never summarised.

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | UUID4 string |
| `project_id` | TEXT | denormalised — also encoded in the file path |
| `role` | TEXT | `user` / `assistant` / `tool` |
| `content_json` | TEXT | raw JSON of the original `content` field |
| `tool_calls_json` | TEXT | structured tool calls if any |
| `code_refs_json` | TEXT | extracted file/line/op references |
| `token_count` | INTEGER | tokenizer estimate when archived |
| `ts` | INTEGER | epoch ms |
| `hash` | TEXT UNIQUE | sha256 over `role + content + tool_calls` for dedup |
| `evicted` | INTEGER CHECK 0/1 | 1 once removed from active context |
| `pinned` | INTEGER CHECK 0/1 | 1 → decay-exempt |
| `hit_count` | INTEGER | incremented when retrieved + cited |
| `memory_type` | TEXT | 5-way: `priority`/`procedural`/`semantic`/`episodic`/`task` |
| `facet_pending` | INTEGER CHECK 0/1 | 1 until facet worker processes |
| `compaction_rescued` | INTEGER CHECK 0/1 | 1 if Vector-3 rescue created it |

### `vec_episodes` (sqlite-vec virtual table)

| column | type |
|---|---|
| `episode_id` | TEXT PK FK→episodes.id |
| `embedding` | FLOAT[768] |
| `memory_type` | TEXT |
| `importance` | REAL |
| `ts` | INTEGER |

### `episodes_fts` (SQLite FTS5)

| column | notes |
|---|---|
| `episode_id` | UNINDEXED — pointer to episodes.id |
| `body` | full text indexed; `tokenize=unicode61 tokenchars './-_:'` preserves compound tokens like `0.85`, `char/4`, `middleware.py:42`, `letsencryptresolver` |

### `seen_turns`

Counter-compaction memory: every assistant turn the proxy has witnessed.

| column | type | notes |
|---|---|---|
| `project_id` | TEXT PK | |
| `turn_hash` | TEXT PK | sha256 of normalised message |
| `turn_index` | INTEGER | position when first seen |
| `content_json` | TEXT | full raw message |
| `first_seen_ts` | INTEGER | |
| `last_seen_ts` | INTEGER | updated on every subsequent appearance; drives prune TTL |

## Kuzu graph (per-project)

### Node tables

| node | properties |
|---|---|
| `Episode` | `id STRING PK, ts INT64, memory_type STRING, importance DOUBLE` |
| `Entity` | `name STRING PK, kind STRING ("file"/"url"/"identifier"/"command")` |
| `File` | `path STRING PK, ext STRING` |
| `Decision` | `hash STRING PK, summary STRING` |
| `Command` | `sig STRING PK, first_seen_ts INT64` |

### Relation tables

| edge | from → to | meaning |
|---|---|---|
| `MENTIONS` | Episode → Entity | episode text references this entity |
| `TOUCHED` | Episode → File | episode performed file op (read/write/edit) |
| `IMPLEMENTS` | Episode → Decision | episode captures or executes a decision |
| `RAN` | Episode → Command | episode ran a bash/powershell command |
| `AFTER` | Episode → Episode | temporal chain — used by causality_chain leg |

## Indexes

| table | index | purpose |
|---|---|---|
| `episodes` | `UNIQUE(hash)` | sha256 dedup |
| `episodes` | `INDEX(evicted, ts)` | retrieval — fetch evicted-and-recent |
| `episodes` | `INDEX(facet_pending)` | facet worker polling |
| `episodes_fts` | FTS5 implicit | BM25 lexical |
| `vec_episodes` | sqlite-vec implicit | cosine MATCH |
| Kuzu | PK on every node table | graph lookups |

## DB growth

Heavy bench (400 turns, 4 archives):

```
~/.spillover/projects/<pid>/episodes.db   3.3 MB
~/.spillover/projects/<pid>/kuzu/         ~12 KB (small graph)
```

Linear in archived episodes. At ~825 KB per archived episode at this density (mostly the raw content_json + embedding blob), 1000 episodes ≈ 800 MB. Real usage with shorter typical turns lands closer to ~100 KB per archive → ~100 MB per 1000 episodes.

`SPILLOVER_RETENTION_DAYS` (planned) + decay scheduler will prune cold rows. Today: no automatic deletion; old episodes simply decay to low importance.
