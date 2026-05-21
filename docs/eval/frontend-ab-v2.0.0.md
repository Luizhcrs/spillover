# Frontend A/B bench — app de delivery (3 telas)

Detalhes ancorados: 12
Lista: Inicio, Carrito, Big Burger Queso, $5.20, $55.00, $12.58, Realizar compra, Ordenar ahora, Hamburguesa especial, Explorar categorias, Productos populares, Recomendados

## Resumo

| metrica | vanilla_truncated | spillover |
|---|---:|---:|
| detalhes citados | 4/12 | 11/12 |
| turnos enviados | 9 | 101 |
| chars enviados | 845 | 6958 |
| input_tokens visivel | 314 | 1063 |
| spillover_real_input_tokens | - | 2640 |
| output_tokens | 7512 | 7686 |
| latencia ms | 35232 | 36000 |
| chars HTML output | 25741 | 22754 |
| erros | 0 | 0 |

## Anchors por modo

### vanilla_truncated
- hit: Inicio, Carrito, Big Burger Queso, Hamburguesa especial
- miss: $5.20, $55.00, $12.58, Realizar compra, Ordenar ahora, Explorar categorias, Productos populares, Recomendados

### spillover
- hit: Inicio, Carrito, Big Burger Queso, $5.20, $12.58, Realizar compra, Ordenar ahora, Hamburguesa especial, Explorar categorias, Productos populares, Recomendados
- miss: $55.00

