# Barbearia A/B bench — sistema completo backend+frontend

Detalhes ancorados: 26

## Resumo

| metrica | vanilla_truncated | spillover |
|---|---:|---:|
| detalhes citados | 8/26 | 22/26 |
| arquivos extraidos | backend.py, frontend.html, schema.sql | backend.py, frontend.html, schema.sql |
| turnos enviados | 9 | 227 |
| chars enviados | 1074 | 9591 |
| input_tokens visivel | 379 | 2116 |
| spillover_real_input_tokens | - | 4202 |
| output_tokens | 8838 | 9069 |
| latency_ms | 42826 | 45123 |
| chars output | 29721 | 30870 |
| erros | 0 | 0 |

## vanilla_truncated
- hit: Carlos, Barba Completa, #1a1a1a, Playfair Display, WhatsApp, backend.py, frontend.html, schema.sql
- miss: Barbearia Tres Tesouras, Rua das Palmeiras 123, (11) 98765-4321, Joao, Pedro, Corte Masculino, Combo Cabelo + Barba, R$ 45, R$ 35, R$ 75, /api/barbeiros, /api/servicos, /api/agendamentos, CREATE TABLE barbeiros, CREATE TABLE servicos, CREATE TABLE agendamentos, #d4a574, 30 minutos

## spillover
- hit: Barbearia Tres Tesouras, Rua das Palmeiras 123, (11) 98765-4321, Joao, Pedro, Carlos, Corte Masculino, Barba Completa, Combo Cabelo + Barba, /api/barbeiros, /api/servicos, /api/agendamentos, CREATE TABLE barbeiros, CREATE TABLE servicos, CREATE TABLE agendamentos, #1a1a1a, #d4a574, Playfair Display, WhatsApp, backend.py, frontend.html, schema.sql
- miss: R$ 45, R$ 35, R$ 75, 30 minutos

