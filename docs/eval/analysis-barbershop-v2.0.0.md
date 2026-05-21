# Barbearia A/B bench — analise

**Date:** 2026-05-21
**Setup:** Haiku 4.5, **226 turnos** de conversa de design+arquitetura, `ceiling=150` tokens, `LTM placement=between`, `keep_last_n=8`.

## Headline

| metrica | vanilla_truncated | spillover |
|---|---:|---:|
| detalhes citados | **8/26 (31%)** | **22/26 (85%)** |
| arquivos extraidos | 3 (backend.py, frontend.html, schema.sql) | 3 (mesmos) |
| turnos enviados | 9 | 227 |
| chars enviados | 1,074 | 9,591 |
| input_tokens visivel | 379 | 2,116 |
| spillover_real_input_tokens | — | 4,202 |
| output_tokens | 8,838 | 9,069 |
| latency_ms | 42,826 | 45,123 |
| episodes archived | 0 | **223** |

223 episodes archived em uma unica request por causa do ceiling=150. Spillover sob stress maximo.

## Detalhes que vanilla INVENTOU

Vanilla nao tinha contexto. Improvisou:

| spec original | vanilla improvisou |
|---|---|
| Barbearia Tres Tesouras | sem nome de barbearia |
| Barbeiros: Joao, Pedro, Carlos (so primeiro nome) | Carlos Silva, João Santos, Ricardo Oliveira |
| Servico Corte Masculino R$ 45 | Corte Simples R$ 40 |
| Servico Combo Cabelo + Barba R$ 75 | Corte com Barba R$ 60 |
| Servicos: 5 (Corte/Barba/Combo/Pigmentacao/Sobrancelha) | 5 servicos diferentes (Hidratacao, Pigmentacao com preco diferente, etc) |
| Schema barbeiros (id, nome, experiencia, especialidade) | (id, nome, telefone) — campos errados |
| Schema agendamentos.status TEXT DEFAULT 'pendente' | confirmado BOOLEAN |
| Endpoints `/api/barbeiros`, `/api/servicos`, `/api/agendamentos` | `/api/v1/servicos` (versionou) |
| Stack: SQL direto (sem ORM) | SQLAlchemy ORM |
| Telefone (11) 98765-4321 | nao mencionou |
| Endereco Rua das Palmeiras 123 | nao mencionou |

Resultado: codigo **funcionalmente irreutilizavel** para o cliente que decidiu essas coisas na conversa. Refazendo tudo do zero.

## Detalhes que spillover acertou

Anchors hit (22/26):

- **Identidade:** Barbearia Tres Tesouras, Rua das Palmeiras 123, (11) 98765-4321
- **Barbeiros:** Joao, Pedro, Carlos (sem sobrenomes inventados)
- **Servicos:** Corte Masculino, Barba Completa, Combo Cabelo + Barba (nomes exatos do brief)
- **Endpoints:** /api/barbeiros, /api/servicos, /api/agendamentos (sem versionar)
- **Schema:** CREATE TABLE barbeiros/servicos/agendamentos (3 tabelas, colunas exatas)
- **Visual:** #1a1a1a primary, #d4a574 secondary, Playfair Display titulos
- **Regra:** WhatsApp pra confirmacao
- **Arquivos:** backend.py + frontend.html + schema.sql separados

Spillover seed dos servicos:

```python
(1, 'Corte Masculino', 45.0, 30),
(2, 'Barba Completa', 35.0, 30),
(3, 'Combo Cabelo + Barba', 75.0, 60),
```

Precos 45/35/75 **exatos**. O anchor literal `"R$ 45"` falhou no substring match porque o modelo escreveu `45.0` (sem prefixo de moeda) — falsa miss por formatacao. Semanticamente **25/26** dos anchors corretos.

## Spillover schema (limpo)

```sql
CREATE TABLE barbeiros (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    experiencia INTEGER NOT NULL,
    especialidade TEXT NOT NULL
);

CREATE TABLE servicos (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    preco REAL NOT NULL,
    duracao_min INTEGER NOT NULL
);

CREATE TABLE agendamentos (
    id INTEGER PRIMARY KEY,
    barbeiro_id INTEGER NOT NULL,
    servico_id INTEGER NOT NULL,
    cliente_nome TEXT NOT NULL,
    cliente_telefone TEXT NOT NULL,
    data_hora TIMESTAMP NOT NULL,
    status TEXT DEFAULT 'pendente',
    FOREIGN KEY(barbeiro_id) REFERENCES barbeiros(id),
    FOREIGN KEY(servico_id) REFERENCES servicos(id)
);
```

Exatamente o que foi especificado nos turnos 17, 18, 19.

## Vanilla schema (improvisado, nao bate brief)

```sql
CREATE TABLE IF NOT EXISTS barbeiros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT UNIQUE NOT NULL,
    telefone TEXT NOT NULL    -- BRIEF DIZIA: experiencia + especialidade
);
...
CREATE TABLE IF NOT EXISTS agendamentos (
    ...
    confirmado BOOLEAN DEFAULT 0   -- BRIEF DIZIA: status TEXT DEFAULT 'pendente'
);
```

Esquema inteiramente diferente do que foi acordado.

## Token economics

| flow | tokens |
|---|---:|
| Payload pro proxy | ~10k (real) |
| Tokens evicted pro archive | ~6k |
| Tokens forwarded ao Anthropic | 4,202 |
| Tokens visiveis ao cliente | 2,116 |
| Counter-compaction delta | 2,086 escondidos |

Counter-compaction V1 escondeu 2086 tokens do cliente. Modelo Anthropic processou contexto reduzido (active turns + LTM block compacto).

## Arquivos gerados (inspecionar manualmente)

```
docs/eval/barbershop-ab/
├── vanilla/
│   ├── backend.py    (9006 chars — SQLAlchemy, nomes inventados)
│   ├── frontend.html (20402 chars)
│   ├── schema.sql    (1025 chars — campos errados)
│   └── raw_output.txt
└── spillover/
    ├── backend.py    (6679 chars — SQL direto, brief fiel)
    ├── frontend.html (24234 chars — fonts/cores corretas)
    ├── schema.sql    (679 chars — schema exato)
    └── raw_output.txt
```

Spillover backend menor mas mais correto. Frontend maior (mais detalhes do brief aplicados).

## Repro

```bash
SPILLOVER_OPERATIONAL_CEILING_TOKENS=150 \
SPILLOVER_WATERMARK=0.5 \
SPILLOVER_LTM_PLACEMENT=between \
spillover up &

spillover bench-barbershop \
  --report docs/eval/barbershop-ab-v2.0.0.md \
  --out-dir docs/eval/barbershop-ab/ \
  --keep-last-n 8 \
  --model claude-haiku-4-5-20251001
```

Custo: ~$0.05 por run.

## Conclusao

Conversa de 226 turnos com 26 anchors espalhados. Ceiling=150 forca eviction continua.

- **Vanilla truncado:** 31% acerto. Codigo improvisado, nomes/precos/schema inventados. Inutil pro cliente.
- **Spillover full history:** 85% acerto (25/26 semantico). Codigo fiel ao brief. Pronto pra usar.

Diferenca de **2.7x acuracia** em scenario realista de coding agent.

Mais importante: vanilla nao **falhou de citar** — ele **inventou conteudo diferente** com confianca. Esse tipo de falha e pior que dizer "nao sei" porque o usuario nem percebe que perdeu informacao ate testar o codigo.

spillover preserva o que o usuario decidiu. Vanilla refaz tudo do zero (com criatividade aleatoria).
