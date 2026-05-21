# spillover — Arquitetura

Documentacao da arquitetura. Cada arquivo e uma vista diferente do sistema; juntos cobrem contexto, topologia em runtime, decomposicao hexagonal, fluxo end-to-end de request, schema de armazenamento, e numeros de performance.

Diagramas em [Mermaid](https://mermaid.js.org/) — renderizam direto no GitHub, Notion, Obsidian, GitLab. Para PowerPoint/PDF: cole o bloco mermaid em [mermaid.live](https://mermaid.live) e exporte PNG/SVG.

## Indice

| # | Vista | Proposito |
|---|---|---|
| [01](01-system-context.md) | Contexto do Sistema | spillover + atores externos |
| [02](02-container.md) | Containers | Containers de runtime + estado por projeto |
| [03](03-component-hexagonal.md) | Componentes (hexagonal) | Camadas Inbound / Aplicacao / Dominio / Outbound |
| [04](04-sequence-hot-path.md) | Sequencia (request inbound) | Fluxo end-to-end de uma chamada |
| [05](05-episode-lifecycle.md) | Maquina de estados | Estados do Episodio: Ativo → Evicted → Embedded → Decayed |
| [06](06-eviction-flow.md) | Data flow (eviction) | Politica overflow token-balanced 1:1 |
| [07](07-retrieval-fusion.md) | Data flow (retrieval) | Fusion hibrido 4-pernas (vector + graph + bm25 + causal) |
| [08](08-counter-compaction.md) | Defesas counter-compaction | Estrategia em 4 vetores |
| [09](09-storage-schema.md) | ER diagram | Schema SQLite + Kuzu por projeto |
| [10](10-performance-heavy.md) | Performance | Numeros do bench heavy |
| [11](11-deployment.md) | Topologia de deploy | Processos locais + cloud |
| [12](12-token-economics.md) | Sankey de tokens | Onde os tokens vao em steady state |

## Ordem de leitura sugerida

- **Overview rapido:** 01 → 04 → 10
- **Deep-dive tecnico:** 02 → 03 → 06 → 07 → 09
- **Numeros + custo:** 10 → 12
- **Setup operacional:** 11 → 02 → 09

## Convencoes dos diagramas

- Mermaid C4 model: caixas vermelhas sao sistemas externos; azuis sao componentes do spillover; verdes sao stores persistentes; roxas sao pessoas.
- "Hexagonal" segue Cockburn (ports & adapters): dominio no centro, inbound adapters dirigem, outbound adapters atendem.
- Numeros vem de `docs/eval/heavy-stress-v1.6.0.md` (run real contra Anthropic Haiku 4.5 em 2026-05-21).

## Relacionados

- [Spec de design original](../superpowers/specs/2026-05-20-spillover-design.md) — design completo do produto.
- [Planos de implementacao](../superpowers/plans/) — Plans 1 a 10 + 8.1.
- [Baselines de avaliacao](../eval/) — resultados publicados de v1.3.0 ate v1.6.1.
