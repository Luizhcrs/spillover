# Contributing to spillover

spillover e Apache-2.0. Contribuicoes sao bem-vindas.

## Setup

```bash
git clone https://github.com/Luizhcrs/spillover
cd spillover
pip install -e ".[dev]"
python -m pytest -v -m "not slow"
```

## Workflow

1. Abra issue descrevendo bug ou proposta antes de PR grande.
2. Branch a partir de `master`. Nome no padrao `feat/<topic>`, `fix/<bug>`, `docs/<area>`.
3. TDD onde aplica — testes em `tests/`.
4. Ruff clean: `python -m ruff check src/ tests/`.
5. Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `perf:`, `test:`).
6. PR contra `master`. Descricao com motivacao + escopo.

## Estilo

- Modulos por feature, nao por camada.
- Domain logic pura (sem I/O) em `eviction/`, `facet/{classifier,entities,decisions,tasks}`, `retriever/{fusion,vector,lexical,graph,causal,budget,render}`, `counter_compact/{usage_rewrite,sse_rewrite,intercept,detection}`.
- I/O em adapters: `archive/writer`, `storage/{sqlite,kuzu}`, `facet/embed`, `proxy/`, `adapters/`.
- Sem emojis em codigo, docs, commits.
- Sem `Co-Authored-By: Claude` em commits.

## Areas onde PRs sao bem-vindos

- Adapters pra novos providers (Bedrock, Vertex AI, etc) em `adapters/`.
- Embedders alternativos em `facet/embed.py` (Plan 8+ targets).
- Wrappers pra outros CLIs (Aider, OpenHands, Roo) em `wrappers/`.
- Dataset de recall@k para `docs/eval/`.
- Otimizacoes em retriever (HyDE, ColBERT, Self-RAG gate) — ver `docs/superpowers/plans/`.
- Documentacao em outros idiomas.

## Codigo de conduta

Trate contribuidores com respeito. Discussoes tecnicas focam em codigo, nao em pessoas.

## Licenca

Ao contribuir voce concorda que sua contribuicao sera licenciada sob Apache-2.0 (ver LICENSE).
