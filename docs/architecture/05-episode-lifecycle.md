# 05 — Ciclo de vida do Episodio

Maquina de estados de um unico turno de conversa, desde o momento em que chega numa request ate sumir da relevancia pro retrieval.

```mermaid
stateDiagram-v2
    [*] --> Active: turno chega no payload
    Active --> Active: fica no contexto ativo<br/>enquanto fill_ratio < watermark

    Active --> Evicted: fill_ratio ≥ watermark<br/>+ selecionado pela politica 3-pass<br/>+ archive_raw INSERT
    Active --> Pinned: spillover pin id<br/>(exempt de decay)

    Evicted --> FacetPending: enfileirado na facet queue
    FacetPending --> FacetPending: fila cheia →<br/>facet_dropped_total++<br/>(retry na proxima request)
    FacetPending --> Embedded: worker processa:<br/>+ embed_text<br/>+ classify (5-way)<br/>+ extract entities/decisions/<br/>code_refs/tasks<br/>+ INSERT vec_episodes<br/>+ MERGE Kuzu nodes+edges<br/>+ facet_pending=0

    Embedded --> Retrieved: matched pela fusion RRF<br/>(vector/graph/bm25/causal)
    Retrieved --> Embedded: nao selecionado na proxima query

    Embedded --> Decayed: importance < threshold<br/>apos decay exponencial
    Decayed --> [*]: ainda queryable mas<br/>sumiu do top-K

    Pinned --> Pinned: decay skippado<br/>(imortal)

    state CompactionRescue {
        [*] --> Detected: diff seen_turns<br/>vs payload inbound
        Detected --> RescuedArchived: archive como<br/>compaction_rescued=1
        RescuedArchived --> [*]
    }

    Active --> CompactionRescue: cliente compactou<br/>apesar do usage rewrite
    CompactionRescue --> FacetPending
```

## Estados

| estado | significado | marcadores no DB |
|---|---|---|
| Active | no `messages[]` da conversa inbound | ainda nao na tabela `episodes` |
| Pinned | ativo OU arquivado mas exempt de decay | `episodes.pinned = 1` |
| Evicted | removido do contexto ativo, conteudo raw preservado | `episodes.evicted = 1, facet_pending = 1` |
| FacetPending | arquivado mas ainda nao indexado | `episodes.facet_pending = 1` |
| Embedded | pipeline de facet completo; queryable | `episodes.facet_pending = 0` + linha `vec_episodes` + linha `episodes_fts` + nodes Kuzu |
| Retrieved | incluido no LTM block atual | transiente — nao e estado persistido |
| Decayed | importance baixa; raramente escolhido pela RRF | `vec_episodes.importance` < threshold |
| RescuedArchived | resgatado de uma compaction client-side | `episodes.compaction_rescued = 1` |

## Transicoes

| de | pra | trigger |
|---|---|---|
| `Active` | `Evicted` | watermark cruzado + selector escolheu + archive_raw succeeded |
| `Active` | `Pinned` | `spillover pin <id>` (placeholder de CLI; UPDATE programatico ja suporta) |
| `Active` | `CompactionRescue` | payload inbound perdeu um turno que vimos antes |
| `Evicted` | `FacetPending` | enfileirado em `asyncio.Queue` |
| `FacetPending` | `Embedded` | facet worker processou o evento |
| `Embedded` | `Retrieved` | matched pela fusion RRF nesta turn |
| `Embedded` | `Decayed` | decay exp reduziu importance abaixo do threshold |
| `Pinned` | `Pinned` (self) | decay scheduler pula linhas pinned |

## Formula do decay

```
importance = base_pro_tipo × exp(-age_hours / half_life_pro_tipo)
           + min(hit_count × 0.05, 0.5)
```

| tipo | base | half_life |
|---|---:|---:|
| priority | 1.0 | 60 dias |
| task | 0.95 | 90 dias |
| procedural | 0.7 | 30 dias |
| semantic | 0.6 | 14 dias |
| episodic | 0.5 | 7 dias |

## Path de storage por estado

```
Active           → no payload em flight (nao persistido pelo spillover)
Evicted          → episodes(content_json) + episodes_fts(body)
FacetPending     → mesma linha, facet_pending=1
Embedded         → + vec_episodes(embedding, importance) + Kuzu nodes/edges
RescuedArchived  → episodes(compaction_rescued=1)
Decayed          → mesma linha, importance menor
Pinned           → episodes(pinned=1), inalterado pelo decay
```
