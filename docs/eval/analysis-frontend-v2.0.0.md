# Frontend A/B bench — analise

**Date:** 2026-05-21
**Setup:** Haiku 4.5, 100 turnos de design conversation, `ceiling=150` tokens, `watermark=0.5`, `LTM placement=between`, `keep_last_n=8`.

## Headline

| metrica | vanilla_truncated | spillover |
|---|---:|---:|
| detalhes acertados | **4/12 (33%)** | **11/12 (92%)** |
| turnos enviados | 9 | 101 |
| chars enviados | 845 | 6,958 |
| input_tokens visivel | 314 | 1,063 |
| spillover_real_input_tokens | — | 2,640 |
| output_tokens | 7,512 | 7,686 |
| chars HTML produzido | 25,741 | 22,754 |
| latency_ms | 35,232 | 36,000 |
| episodes archived | 0 | 97 |

Ceiling=150 forcou eviction em 97 dos 100 turnos da historia. Spillover engrenagem em uso pesado.

## Anchors por modo

**Vanilla 4/12 — soh nomes genericos / recentes:**
- Inicio, Carrito, Big Burger Queso, Hamburguesa especial

**Spillover 11/12 — quase tudo:**
- Inicio, Carrito, Big Burger Queso, $5.20, $12.58, Realizar compra, Ordenar ahora, Hamburguesa especial, Explorar categorias, Productos populares, Recomendados
- soh missou: $55.00

## HTML quality delta

Comparacao por inspecao dos arquivos:

| caracteristica | vanilla | spillover |
|---|---|---|
| Tailwind CDN | nao | **sim** (`<script src="https://cdn.tailwindcss.com">`) |
| Inter font especificada | nao | **sim** (`font-family: 'Inter'`) |
| Paleta exata | aproximada | **#7C3AED + #6D28D9 + #06B6D4 exatos** |
| Lucide icons | nao mencionado | **sim** (CDN incluido) |
| Border-radius scale | generico | **12/24/999 conforme spec** |
| Box-shadow scale | generico | **0 4px 6px rgba(0,0,0,0.05) exato** |
| Espacamento 4-base | generico | **multiplos de 4 respeitados** |
| Placeholder images | generic | **placehold.co com cores brand** |
| Copy em espanhol | parcial | **literal completo** |

Vanilla improvisou. Spillover seguiu o brief.

## Por que vanilla deu 33%

`keep_last_n=8` simula compaction. Modelo viu os 8 ultimos turnos da historia + a pergunta final. Os 8 ultimos eram detalhes de implementacao tardios (cores recentes, Tailwind CDN, layout flex, dimensoes 375x812, placehold.co URLs).

Detalhes plantados nos turnos iniciais (precos $5.20/$12.58/$55.00, nomes das secoes "Explorar categorias"/"Productos populares"/"Recomendados", copy literal "Realizar compra"/"Ordenar ahora") foram cortados.

Modelo improvisou nomes alternativos ("Categories", "Popular Items"), precos aleatorios, sem CTAs especificos.

## Por que spillover deu 92%

Pipeline:
1. 100 turnos enviados ao proxy
2. Ceiling=150 ativa eviction ainda na primeira request
3. 97 episodes archived raw
4. Facet pipeline encodes embeddings + entities + decisions
5. Retriever (vector + BM25 + graph + causal) busca matches pra pergunta final
6. RRF fusion devolve top-K com detalhes plantados nos turnos 1-30
7. LTM block injetado entre active turns e final user turn
8. Modelo le LTM como historico sintetico, cita verbatim

LTM contem precos + CTAs + nomes das secoes — modelo aplica.

## Por que falhou em $55.00

Spillover acertou $5.20 (preco unitario do Big Burger Queso) mas missou $55.00 (total).

Hypothesis: o turno que estabeleceu Total $55.00 (turno 19 na historia) provavelmente nao apareceu no top-K. Detalhes especificos com baixa similaridade com a pergunta final ("Total do carrito" vs pergunta generica "gera HTML") ficaram fora.

Plan 11 candidate: melhorar query expansion (HyDE) ou usar todos os turnos com tipo=task como guaranteed-include no LTM.

## Inspecao visual

`docs/eval/frontend-ab/vanilla.html` e `docs/eval/frontend-ab/spillover.html` salvos.

Spillover HTML usa Tailwind classes, gradient correto, layout fiel ao brief. Vanilla HTML usa inline styles, gradient generico, layout aproximado.

## Token economics

| flow | tokens |
|---|---:|
| Conversa enviada ao proxy | ~3000 (real Anthropic count) |
| Tokens evicted pro archive | ~1577 (delta entre real 2640 e visible 1063) |
| Tokens forwarded ao Anthropic com LTM | 2640 |
| Reducao via eviction | ~60% |

Counter-compaction V1 escondeu 1577 tokens do cliente. Modelo recebeu LTM block compacto + ultimo turno do user + alguns active turns.

## Repro

```bash
SPILLOVER_OPERATIONAL_CEILING_TOKENS=150 \
SPILLOVER_WATERMARK=0.5 \
SPILLOVER_LTM_PLACEMENT=between \
spillover up &

spillover bench-frontend \
  --report docs/eval/frontend-ab-v2.0.0.md \
  --out-dir docs/eval/frontend-ab/ \
  --keep-last-n 8 \
  --model claude-haiku-4-5-20251001
```

Custo: ~$0.04 por run.

## Conclusao

Em conditions adversariais extremas (ceiling=150 forcando eviction continua), spillover **preservou 92% dos detalhes named** vs vanilla truncado **33%**. Diferenca de 2.8x.

O HTML output do spillover e visualmente fiel ao brief; o do vanilla improvisa generico.
