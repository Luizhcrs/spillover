# 09 — Schema de armazenamento (por projeto)

Cada projeto tem seu proprio par SQLite + Kuzu em `~/.spillover/projects/<sha1(cwd)>/`.

```mermaid
erDiagram
    episodes ||--o| vec_episodes : "1:1 (apos facet)"
    episodes ||--o| episodes_fts : "1:1 (espelho FTS5)"
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
        TEXT body "FTS5 tokenize unicode61 tokenchars dot slash dash underscore colon"
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

## Tabelas SQLite

### `episodes`

Fonte da verdade pro conteudo dos turnos arquivados. Raw, nunca resumido.

| coluna | tipo | notas |
|---|---|---|
| `id` | TEXT PK | string UUID4 |
| `project_id` | TEXT | denormalizado — tambem encoded no path do arquivo |
| `role` | TEXT | `user` / `assistant` / `tool` |
| `content_json` | TEXT | JSON raw do campo `content` original |
| `tool_calls_json` | TEXT | tool calls estruturadas se houver |
| `code_refs_json` | TEXT | referencias file/line/op extraidas |
| `token_count` | INTEGER | estimativa do tokenizer no momento do archive |
| `ts` | INTEGER | epoch ms |
| `hash` | TEXT UNIQUE | sha256 sobre `role + content + tool_calls` pra dedup |
| `evicted` | INTEGER CHECK 0/1 | 1 quando removido do contexto ativo |
| `pinned` | INTEGER CHECK 0/1 | 1 → exempt de decay |
| `hit_count` | INTEGER | incrementa quando retrieved + citado |
| `memory_type` | TEXT | 5-way: `priority`/`procedural`/`semantic`/`episodic`/`task` |
| `facet_pending` | INTEGER CHECK 0/1 | 1 ate o facet worker processar |
| `compaction_rescued` | INTEGER CHECK 0/1 | 1 se rescue do Vector-3 criou ele |

### `vec_episodes` (tabela virtual sqlite-vec)

| coluna | tipo |
|---|---|
| `episode_id` | TEXT PK FK→episodes.id |
| `embedding` | FLOAT[768] |
| `memory_type` | TEXT |
| `importance` | REAL |
| `ts` | INTEGER |

### `episodes_fts` (SQLite FTS5)

| coluna | notas |
|---|---|
| `episode_id` | UNINDEXED — pointer pra episodes.id |
| `body` | texto inteiro indexado; `tokenize=unicode61 tokenchars './-_:'` preserva tokens compostos tipo `0.85`, `char/4`, `middleware.py:42`, `letsencryptresolver` |

### `seen_turns`

Memoria de counter-compaction: todo turno assistente que o proxy ja viu.

| coluna | tipo | notas |
|---|---|---|
| `project_id` | TEXT PK | |
| `turn_hash` | TEXT PK | sha256 da mensagem normalizada |
| `turn_index` | INTEGER | posicao quando visto pela primeira vez |
| `content_json` | TEXT | mensagem raw inteira |
| `first_seen_ts` | INTEGER | |
| `last_seen_ts` | INTEGER | atualizado em toda aparicao subsequente; driva TTL do prune |

## Grafo Kuzu (por projeto)

### Tabelas de node

| node | propriedades |
|---|---|
| `Episode` | `id STRING PK, ts INT64, memory_type STRING, importance DOUBLE` |
| `Entity` | `name STRING PK, kind STRING ("file"/"url"/"identifier"/"command")` |
| `File` | `path STRING PK, ext STRING` |
| `Decision` | `hash STRING PK, summary STRING` |
| `Command` | `sig STRING PK, first_seen_ts INT64` |

### Tabelas de relacao

| edge | de → pra | significado |
|---|---|---|
| `MENTIONS` | Episode → Entity | texto do episodio referencia essa entidade |
| `TOUCHED` | Episode → File | episodio fez op no arquivo (read/write/edit) |
| `IMPLEMENTS` | Episode → Decision | episodio captura ou executa uma decisao |
| `RAN` | Episode → Command | episodio rodou um comando bash/powershell |
| `AFTER` | Episode → Episode | cadeia temporal — usado pela perna causality_chain |

## Indices

| tabela | indice | proposito |
|---|---|---|
| `episodes` | `UNIQUE(hash)` | dedup sha256 |
| `episodes` | `INDEX(evicted, ts)` | retrieval — fetch evicted-and-recent |
| `episodes` | `INDEX(facet_pending)` | polling do facet worker |
| `episodes_fts` | FTS5 implicit | BM25 lexical |
| `vec_episodes` | sqlite-vec implicit | cosine MATCH |
| Kuzu | PK em toda node table | lookups de grafo |

## Crescimento do DB

Heavy bench (400 turnos, 4 archives):

```
~/.spillover/projects/<pid>/episodes.db   3.3 MB
~/.spillover/projects/<pid>/kuzu/         ~12 KB (grafo pequeno)
```

Linear no contador de episodios arquivados. Com ~825 KB por episodio nessa densidade (principalmente o content_json raw + blob do embedding), 1000 episodios ≈ 800 MB. Uso real com turnos tipicos mais curtos da mais perto de ~100 KB por archive → ~100 MB por 1000 episodios.

`SPILLOVER_RETENTION_DAYS` (planejado) + decay scheduler vao fazer prune de linhas frias. Hoje: sem delecao automatica; episodios velhos soh decaem pra importance baixa.
