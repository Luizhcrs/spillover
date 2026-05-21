# spillover — Architecture

Corporate-grade architecture documentation. Each file is one view; together they cover system context, runtime topology, hexagonal decomposition, end-to-end request flow, storage schema, and performance evidence.

All diagrams use [Mermaid](https://mermaid.js.org/) — renders natively in GitHub, Notion, Obsidian, GitLab, and Anthropic web. For PowerPoint/PDF: paste the mermaid block into [mermaid.live](https://mermaid.live) and export PNG/SVG.

## Index

| # | View | Purpose | Audience |
|---|---|---|---|
| [01](01-system-context.md) | System Context | spillover + external actors | Executive, BD |
| [02](02-container.md) | Container | Runtime containers + per-project state | Architect |
| [03](03-component-hexagonal.md) | Component (hexagonal) | Inbound / Application / Domain / Outbound layers | Engineer |
| [04](04-sequence-hot-path.md) | Sequence (inbound request) | End-to-end request flow | Engineer |
| [05](05-episode-lifecycle.md) | State machine | Episode states: Active → Evicted → Embedded → Decayed | Engineer |
| [06](06-eviction-flow.md) | Data flow (eviction) | Token-balanced 1:1 overflow policy | Engineer |
| [07](07-retrieval-fusion.md) | Data flow (retrieval) | 4-leg hybrid fusion (vector + graph + bm25 + causal) | Engineer |
| [08](08-counter-compaction.md) | Counter-compaction defenses | Four-vector strategy | Engineer, Security |
| [09](09-storage-schema.md) | ER diagram | Per-project SQLite + Kuzu schema | Engineer, DBA |
| [10](10-performance-heavy.md) | Performance bars | Heavy-stress bench numbers | All |
| [11](11-deployment.md) | Deployment topology | Workstation-local processes + cloud | Operator |
| [12](12-token-economics.md) | Token sankey | Where the tokens go (steady state) | Architect, BD |

## Reading order

- **For a 5-minute overview**: 01 → 04 → 10
- **For an architect deep-dive**: 02 → 03 → 06 → 07 → 09
- **For an investor pitch**: 01 → 10 → 12
- **For an operator runbook**: 11 → 02 → 09

## Doc conventions

- Mermaid C4 model: red boxes are external systems; blue are spillover-owned; green are persistent stores; purple are people.
- "Hexagonal" follows Cockburn ports & adapters: domain at center, inbound adapters drive, outbound adapters serve.
- Numbers from `docs/eval/heavy-stress-v1.6.0.md` (real Anthropic Haiku 4.5 run, 2026-05-21).

## Related

- [Design spec](../superpowers/specs/2026-05-20-spillover-design.md) — full original design.
- [Plans](../superpowers/plans/) — 10 implementation plans Plans 1–10 + 8.1.
- [Evaluation](../eval/) — published baselines v1.3.0 through v1.6.1.
