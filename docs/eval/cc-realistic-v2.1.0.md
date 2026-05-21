# CC-realistic A/B — barbearia full system

Detalhes ancorados: 26

## Resumo

| metrica | cc_realistic (vanilla com compaction) | spillover |
|---|---:|---:|
| detalhes citados | 22/26 | 25/26 |
| arquivos extraidos | backend.py, frontend.html, schema.sql | backend.py, frontend.html, schema.sql |
| turnos input | 500 | 500 |
| chars input | 21604 | 22364 |
| compaction events | 4 | 0 |
| summary tokens gastos (vanilla) | 12116 | 0 |
| input_tokens final | 2698 | 3958 |
| spillover_real_input_tokens | - | 9150 |
| output_tokens | 8347 | 9164 |
| custo USD estimado | $0.0661 | $0.0550 |
| latency_ms | 80732 | 42960 |
| erros | 0 | 0 |

## cc_realistic
- hit: Barbearia Tres Tesouras, Rua das Palmeiras 123, (11) 98765-4321, Joao, Pedro, Carlos, Corte Masculino, Barba Completa, /api/barbeiros, /api/servicos, /api/agendamentos, CREATE TABLE barbeiros, CREATE TABLE servicos, CREATE TABLE agendamentos, #1a1a1a, #d4a574, Playfair Display, 30 minutos, WhatsApp, backend.py, frontend.html, schema.sql
- miss: Combo Cabelo + Barba, R$ 45, R$ 35, R$ 75

## spillover
- hit: Rua das Palmeiras 123, (11) 98765-4321, Joao, Pedro, Carlos, Corte Masculino, Barba Completa, Combo Cabelo + Barba, R$ 45, R$ 35, R$ 75, /api/barbeiros, /api/servicos, /api/agendamentos, CREATE TABLE barbeiros, CREATE TABLE servicos, CREATE TABLE agendamentos, #1a1a1a, #d4a574, Playfair Display, 30 minutos, WhatsApp, backend.py, frontend.html, schema.sql
- miss: Barbearia Tres Tesouras

