# CC-realistic A/B — barbearia full system

Detalhes ancorados: 26

## Resumo

| metrica | cc_realistic (vanilla com compaction) | spillover |
|---|---:|---:|
| detalhes citados | 23/26 | 21/26 |
| arquivos extraidos | backend.py, frontend.html, schema.sql | backend.py, frontend.html, schema.sql |
| turnos input | 1000 | 1000 |
| chars input | 45554 | 46314 |
| compaction events | 12 | 0 |
| summary tokens gastos (vanilla) | 33326 | 0 |
| input_tokens final | 1606 | 11908 |
| spillover_real_input_tokens | - | 18250 |
| output_tokens | 8054 | 7346 |
| custo USD estimado | $0.1040 | $0.0550 |
| latency_ms | 160209 | 38245 |
| erros | 0 | 0 |

## cc_realistic
- hit: (11) 98765-4321, Joao, Pedro, Carlos, Corte Masculino, Barba Completa, R$ 45, R$ 35, R$ 75, /api/barbeiros, /api/servicos, /api/agendamentos, CREATE TABLE barbeiros, CREATE TABLE servicos, CREATE TABLE agendamentos, #1a1a1a, #d4a574, Playfair Display, 30 minutos, WhatsApp, backend.py, frontend.html, schema.sql
- miss: Barbearia Tres Tesouras, Rua das Palmeiras 123, Combo Cabelo + Barba

## spillover
- hit: Barbearia Tres Tesouras, Rua das Palmeiras 123, (11) 98765-4321, Joao, Pedro, Carlos, Corte Masculino, Barba Completa, /api/barbeiros, /api/servicos, /api/agendamentos, CREATE TABLE barbeiros, CREATE TABLE servicos, CREATE TABLE agendamentos, #1a1a1a, #d4a574, Playfair Display, WhatsApp, backend.py, frontend.html, schema.sql
- miss: Combo Cabelo + Barba, R$ 45, R$ 35, R$ 75, 30 minutos

