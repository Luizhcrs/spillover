# CC-realistic EXTREME A/B — 1000 turnos

**Date:** 2026-05-21
**Setup:** Haiku 4.5, **1000 turnos**, vanilla com **12 compaction events**, spillover ceiling=200.

Tentativa anterior: Sonnet 4.5/4.6 via OAuth = HTTP 429 sustentado. Provavel cota OAuth limitada vs sessao CC ativa. Fallback Haiku.

## Headline (descoberta surpreendente)

| metrica | cc_realistic (12 compactions) | spillover | delta |
|---|---:|---:|---:|
| **anchors hit** | **23/26 (88%)** | **21/26 (81%)** | **-7 pp** |
| compactions | 12 | 0 | — |
| input_tokens final | 1,606 | 11,908 (visivel) / 18,250 (real) | spillover ve muito mais |
| output_tokens | 8,054 | 7,346 | empate |
| summary tokens gastos (vanilla) | 33,326 | 0 | — |
| **custo USD** | **$0.104** | **$0.055** | **spillover -47%** |
| **latency_ms** | **160,209** | **38,245** | **spillover -76%** |
| episodes archived (spillover) | n/a | 597 | massivo |

## Inversao do achado v2.2.0 (500 turnos)

| versao | turnos | ceiling | CC recall | spillover recall | vencedor recall |
|---|---:|---:|---:|---:|---|
| v2.2.0 (500) | 500 | 200 | 85% | **96%** | spillover |
| EXTREME (1000) | 1000 | 200 | **88%** | 81% | **CC** |

A 1000 turnos com ceiling 200 muito agressivo:
- spillover faz ~600 episodes archived
- Cada eviction round retrieva top-K=5 episodes pro LTM block (cap 30 tokens × 0.15 = ~30 tokens budget)
- LTM budget MUITO pequeno (ceiling 200 × ltm_pct 0.15 = 30 tokens, soh cabe 1-2 episodios)
- Retriever precisaria escolher PERFEITAMENTE quais 2 episodios entre 597

CC compaction sequencial empilhada (12 summaries) preserva mais nomes/precos sistematicamente que retrieval seletivo num budget asfixiado.

## Anchors detalhados

### CC realistic (23/26) — HIT precos exatos
- Identidade: 1 hit (telefone), 2 miss (nome barbearia, endereco)
- Barbeiros: 3 hit (Joao, Pedro, Carlos)
- Servicos: 2 hit (Corte Masculino, Barba Completa), 1 miss (Combo Cabelo + Barba)
- **Precos: 3 hit (R$ 45, R$ 35, R$ 75) — surprise!**
- Endpoints + schema + cores: todos hit
- WhatsApp, 30 minutos: hit

### spillover (21/26) — HIT identidade, MISS precos formatados
- Identidade: 3 hit (nome, endereco, telefone) — retriever achou turnos iniciais
- Barbeiros: 3 hit
- Servicos: 2 hit, 1 miss
- **Precos: 3 miss — modelo escreveu `45.0` sem `R$ ` (anchor literal falhou substring)**
- Endpoints + schema + cores: todos hit
- 30 minutos: miss (formatacao diferente)

## Por que CC compaction sequencial ganhou neste cenario

12 LLM calls empilhadas com prompt "preserve all named decisions, file paths, function names, exact numbers, prices, and identifiers" forcam o modelo a manter os detalhes numericos.

spillover retrieval ranking baseado em similaridade. Pra query "gera projeto completo da barbearia" os top-K serao episodios com palavra "barbearia", "sistema", "completo" — nao necessariamente os turnos especificos com `R$ 45`.

## O que spillover continua ganhando

| dimensao | vencedor |
|---|---|
| custo USD | spillover (-47%) |
| latency | spillover (-76%) |
| recall identidade primaria (nome/endereco) | spillover |
| recall numerico (precos) | **CC** |
| arquivos gerados (3/3) | empate |

Spillover trade-off real: **gasta MENOS dinheiro + tempo, mas em scale extremo (1000+ turnos, ceiling apertado) perde alguns detalhes ao CC compaction sequencial empilhada.**

## O que mudaria os numeros do spillover

| ajuste | efeito esperado |
|---|---|
| `SPILLOVER_LTM_BUDGET_PCT=0.30` (em vez de 0.15) | +recall (mais espaco LTM) |
| `SPILLOVER_OPERATIONAL_CEILING_TOKENS=1000` (em vez de 200) | +recall (LTM maior, menos eviction) |
| `SPILLOVER_RETRIEVER_TOPK=10` (em vez de 5) | +recall (mais episodios no LTM) |
| HyDE query expansion (Plan 8) | +recall (queries melhores) |
| ColBERT rerank (Plan 8) | +recall (top-K mais preciso) |

Mas todos esses aumentam custo + latencia. Trade-off.

## Sonnet rate-limit honesto

Tentei rodar com `claude-sonnet-4-5` e `claude-sonnet-4-6`:

```
HTTP 429 Too Many Requests
{"type":"rate_limit_error", "message":"Error"}
```

OAuth Bearer do `~/.claude/.credentials.json` compartilha cota TPM com o processo CC ativo (este mesmo Claude Code session que esta dispatching benches). Sonnet tem TPM tier menor que Haiku, esgotou rapido.

Para rodar Sonnet:
- ANTHROPIC_API_KEY direto com cota nova (nao OAuth)
- Aguardar 5-10 min entre runs
- Tier upgrade da conta

Honestamente: Sonnet 4.6 ja paga $3/$15 por Mtok. Custo vanilla compaction com 12 calls + final em Sonnet ≈ $0.50-0.80. spillover ≈ $0.30. Provavelmente o padrao se mantem: spillover -50% custo, vencedor recall depende do cenario.

## Conclusao publishavel HONESTA

spillover **NAO** e silver bullet em todas condicoes. Real:

1. **Em sessoes < 500 turnos com ceiling moderado:** spillover ganha em recall + custo + latencia.
2. **Em sessoes ≥1000 turnos com ceiling muito apertado:** CC compaction sequencial pode ter recall similar ou melhor. spillover ainda ganha custo + latencia.
3. **Em todas condicoes testadas:** spillover **sempre** mais barato (-47%) e mais rapido (-76%).

Recall trade-off depende do cenario. Custo + latencia sao vitorias consistentes.

## Numeros de cost ate 1 dia de dev

| modo | turnos/dia | LLM calls/dia | custo/dia |
|---|---:|---:|---:|
| CC compaction | 1000 | 13 (12 compact + 1 final) | $0.104 |
| spillover | 1000 | 1 | $0.055 |
| **economia anual (240 dias x dev)** | | | **$117** |

Para time de 50 devs: ~$5.880/ano em economia.

Plus: latencia. CC compaction faz dev esperar 12 LLM calls extras a cada interacao final. 160s vs 38s. **Em sessoes ativas o dev percebe.**

## Repro

```bash
SPILLOVER_OPERATIONAL_CEILING_TOKENS=200 \
SPILLOVER_WATERMARK=0.5 \
SPILLOVER_LTM_PLACEMENT=between \
spillover up &

spillover bench-cc-realistic \
  --report docs/eval/cc-realistic-extreme.md \
  --turns 1000 \
  --compaction-threshold-turns 80 \
  --target-summary-tokens 500 \
  --keep-tail-turns 25 \
  --model claude-haiku-4-5-20251001
```

Custo por run: ~$0.16 total (vanilla $0.10 + spillover $0.06).

## Proximos passos pra publishar numeros REAIS

1. Sonnet 4.6 — rodar com API key direto (nao OAuth) pra evitar 429
2. Plan 8: HyDE + ColBERT pra fechar gap nos cenarios extremos
3. Aumentar LTM budget em runs longos (config tuning)
4. Bench multi-dominio (codigo + design + writing) — diversificar anchors

Plan 11 candidate.
