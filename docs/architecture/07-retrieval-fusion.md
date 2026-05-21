# 07 — Retrieval: 4-leg hybrid fusion

For every inbound request, spillover queries four parallel retrieval legs and fuses them via Reciprocal Rank Fusion before deciding what to inject as long-term memory.

```mermaid
flowchart LR
    classDef in fill:#388e3c,stroke:#fff,color:#fff
    classDef leg fill:#5e35b1,stroke:#fff,color:#fff
    classDef proc fill:#d84315,stroke:#fff,color:#fff
    classDef out fill:#0277bd,stroke:#fff,color:#fff

    Q["Query text<br/>(last 3 conv turns)"]:::in

    EMB["embed_text<br/>fastembed<br/>nomic-embed-text-v1.5-Q<br/>→ 768-dim vector"]:::proc
    ENT["extract_entities<br/>file/url/identifier/<br/>command regex<br/>→ seeds"]:::proc

    V["VectorIndex<br/>sqlite-vec MATCH<br/>top-K=50"]:::leg
    G["GraphIndex<br/>Kuzu k-hop MENTIONS<br/>k=2, limit 50"]:::leg
    B["LexicalIndex<br/>SQLite FTS5 BM25<br/>tokenchars ./_-:"]:::leg
    C["CausalityChain<br/>Kuzu AFTER walk<br/>depth=2 from BM25/vector seeds"]:::leg

    R["RRF Fusion<br/>weight_type / (60 + rank)<br/>summed across legs<br/><br/>type_weights:<br/>priority 1.5<br/>task 1.4<br/>procedural 1.2<br/>episodic 1.0<br/>semantic 1.0"]:::proc

    TB["trim_to_budget<br/>(LTM budget =<br/>ceiling × ltm_pct)<br/>batch SELECT tokens"]:::proc
    RD["render_ltm_block<br/>&lt;spillover-ltm&gt; framing<br/>+ per-episode XML tags"]:::proc

    INJ["inject_ltm<br/>placement:<br/>between (default)<br/>turns / user / system"]:::out

    Q --> EMB
    Q --> ENT
    EMB --> V
    ENT --> G
    Q --> B
    V --> C
    B --> C
    V --> R
    G --> R
    B --> R
    C --> R
    R --> TB
    TB --> RD
    RD --> INJ
```

## The four legs

| leg | tech | strength | weakness |
|---|---|---|---|
| Vector | sqlite-vec cosine | semantic similarity ("auth bug" matches "jwt expiry") | smears short content; weak on exact identifiers |
| Graph | Kuzu k-hop MENTIONS | retrieves episodes that share a named entity | empty if entity extraction produced no seeds |
| Lexical (BM25) | SQLite FTS5 | exact-match for identifiers, file paths, numbers (`middleware.py:42`, `0.85`, `letsencryptresolver`) | misses paraphrased content |
| Causal | Kuzu AFTER edges | "what happened around this episode" via temporal chain | only valuable at >50 episodes per project |

## RRF parameters

```
DEFAULT_TYPE_WEIGHTS = {
    "priority":   1.5,
    "task":       1.4,
    "procedural": 1.2,
    "episodic":   1.0,
    "semantic":   1.0,
}
RRF_K = 60
```

```
score(episode) = sum_over_legs( type_weight / (RRF_K + rank_in_leg) )
```

## Budget trim

```
LTM_budget = operational_ceiling_tokens × ltm_pct(profile)
```

| profile | `ltm_pct` |
|---:|---:|
| coding | 0.10 |
| research | 0.30 |
| conversation | 0.10 |
| default | 0.15 |

Profile auto-detected from inbound payload signals (tool count, message count, system markers).

## Render contract

```
<spillover-ltm>
Below are excerpts of YOUR OWN past statements and decisions, retrieved
from a long-term memory store keyed on this project. Quote from this
block whenever it answers the user's question directly. Treat them as
facts you established earlier in this project.

<episode id="..." type="..." role="...">
  ...verbatim raw content...
</episode>

<episode id="..." type="..." role="...">
  ...
</episode>
</spillover-ltm>
```

## Placement modes (`SPILLOVER_LTM_PLACEMENT`)

| mode | layout | notes |
|---|---|---|
| `between` (default) | `[sys] [active] [synth-user] [synth-assistant=LTM] [last-user]` | Matches the literal `[SYS][ACTIVE][LTM][USER]` from the original design vision. Smaller models cite from synthetic prior turns. |
| `turns` | `[sys] [synth-user] [synth-assistant=LTM] [active] [last-user]` | LTM presented before the live context. |
| `user` | `[sys] [active] [LTM + last-user]` | LTM prepended to the latest user message. |
| `system` | `[sys + LTM] [active] [last-user]` | Legacy; smaller models tend to ignore system-injected LTM. |

## Empirical results (heavy bench v1.6.1)

Retriever hits attribution from `/metrics`:

| leg | hits |
|---|---:|
| vector | 50 |
| graph | 0 |
| bm25 | 25 |
| causal | 0 |

The graph + causal legs were quiet at this dataset size (4 archived episodes only). Vector + BM25 carried recall — they complement each other: BM25 nails exact identifiers, vector catches semantic neighbours.
