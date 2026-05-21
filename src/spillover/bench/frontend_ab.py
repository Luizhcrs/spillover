"""Frontend A/B bench — reproduzir telas do app de delivery food.

Stress test extremo: ceiling=150 tokens forca eviction em quase toda turn.
Bench compara modelo gerar HTML do app:

  vanilla_truncated  — modelo so ve as N ultimas mensagens (simula compaction)
  spillover          — full 60 turnos passam pelo proxy com ceiling=150,
                       eviction continua, retrieval reinjeta detalhes

Spec do app:
  3 telas mobile: Inicio (categorias + populares + recomendados),
  Detalhe Hamburguesa, Carrito.
  Cor primaria roxo gradient, accent ciano.
  Preco Big Burger Queso $5.20 cada, Total $55.00.
  CTA "Realizar compra" no carrito.
  CTA "Ordenar ahora" no detalhe.
  Preco hamburguesa especial $12.58.

Avalia recall de detalhes especificos no HTML produzido.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass
class FrontendResult:
    mode: str
    turns_sent: int
    chars_sent: int
    html_out: str
    input_tokens: int
    output_tokens: int
    real_input_tokens: int
    anchors_hit: list[str]
    anchors_missed: list[str]
    latency_ms: int
    error: str | None = None


# Detalhes especificos que devem aparecer no HTML final
DETAIL_ANCHORS = [
    "Inicio",
    "Carrito",
    "Big Burger Queso",
    "$5.20",
    "$55.00",
    "$12.58",
    "Realizar compra",
    "Ordenar ahora",
    "Hamburguesa especial",
    "Explorar categorias",
    "Productos populares",
    "Recomendados",
]


def _build_history() -> list[dict]:
    """Constroi conversa 60-turn estabelecendo todas decisoes de design."""
    turns: list[dict] = []

    decisoes = [
        ("Vamos comecar o brief do app de delivery. Sao 3 telas mobile: Inicio, Detalhe e Carrito.",  # noqa: E501
         "Entendido. 3 telas: Inicio, Detalhe (do hamburguer), Carrito (carrinho)."),
        ("Tela Inicio tem header com titulo 'Inicio' e icone de search. Logo abaixo, categorias horizontais scroll.",  # noqa: E501
         "Anotado: header 'Inicio' + search, scroll horizontal de categorias."),
        ("Categorias se chamam 'Explorar categorias'. Sao circulos com emojis de comida.",
         "Secao 'Explorar categorias' com cards circulares + emojis."),
        ("Logo abaixo, secao 'Productos populares' com cards horizontais — imagem, nome, preco.",  # noqa: E501
         "'Productos populares' em carrossel horizontal: foto + titulo + preco."),
        ("Mais embaixo, secao 'Recomendados' com cards de bebidas — sucos, smoothies. Cards verticais com background pastel.",  # noqa: E501
         "'Recomendados' com cards verticais pastel, focados em drinks."),
        ("Bottom nav tab tem 4 icones: home, search, cart, profile. Cart com badge numerico vermelho.",  # noqa: E501
         "4 tabs: home/search/cart/profile. Cart badge vermelho com contador."),
        ("Cor primaria do app e roxo gradient: #7C3AED para #6D28D9. Accent ciano #06B6D4 pra detalhes.",  # noqa: E501
         "Paleta: roxo primario gradient #7C3AED -> #6D28D9. Ciano accent #06B6D4."),
        ("Background geral branco #FFFFFF. Texto principal preto #111827, texto secundario cinza #6B7280.",  # noqa: E501
         "BG #FFFFFF, texto #111827 primary + #6B7280 secondary."),
        ("Tela 2 (Detalhe) abre quando usuario clica num produto. Header com seta voltar + icone heart.",  # noqa: E501
         "Detalhe: header com back + heart icon."),
        ("Imagem do produto cobre o topo metade da tela. Imagem destaca 'Hamburguesa especial' — burger artesanal.",  # noqa: E501
         "Hero image fullbleed do 'Hamburguesa especial' no topo."),
        ("Logo abaixo da imagem: titulo 'Hamburguesa especial', categoria, e rating em estrelas.",  # noqa: E501
         "Titulo + rating em estrelas embaixo da hero image."),
        ("Secao 'Descripcion' com lorem-ipsum-style placeholder descrevendo a hamburguesa.",  # noqa: E501
         "Bloco 'Descripcion' com texto placeholder."),
        ("Secao 'Ingredientes' com lista horizontal scroll de ingredientes — cada um e card com foto + nome.",  # noqa: E501
         "'Ingredientes' em carrossel horizontal: card foto+nome."),
        ("Preco da hamburguesa especial e $12.58. Posicionado no canto inferior direito acima do CTA.",  # noqa: E501
         "Preco $12.58 bottom-right acima do botao."),
        ("CTA 'Ordenar ahora' e botao roxo gradient full-width na parte inferior. Border-radius alto.",  # noqa: E501
         "CTA 'Ordenar ahora' full-width roxo gradient + border-radius generoso."),
        ("Tela 3 (Carrito) tem header com seta voltar + titulo 'Carrito' + badge contador.",  # noqa: E501
         "Carrito: header back + titulo + badge."),
        ("Cards verticais com cada item do carrinho. Foto, nome, controles -/+, preco unitario.",  # noqa: E501
         "Cards de item: foto + nome + qty controls + preco."),
        ("Primeiro item no carrito e 'Big Burger Queso'. Preco unitario $5.20.",
         "Item 1: Big Burger Queso $5.20."),
        ("Total do carrito mostrado no rodape: $55.00 usd em fonte grande.",
         "Total: $55.00 usd, fonte grande no rodape."),
        ("CTA 'Realizar compra' e gradient roxo full-width na parte inferior. Acima do bottom-nav.",  # noqa: E501
         "CTA 'Realizar compra' roxo gradient full-width."),
        ("Bottom-nav em todas as telas tem o icone do cart com badge mostrando quantidade. No Carrito esse badge sempre mostra o numero atual de itens.",  # noqa: E501
         "Cart badge dinamico baseado em qty."),
        ("Tipografia: usar Inter, fallback sans-serif. Pesos: 400 normal, 500 medium, 700 bold.",  # noqa: E501
         "Inter 400/500/700."),
        ("Sombras: cards levam box-shadow leve, 0 4px 6px rgba(0,0,0,0.05).",
         "Box-shadow 0 4px 6px rgba(0,0,0,0.05) nos cards."),
        ("Border-radius padrao: 12px nos cards, 24px no CTA principal, 999px (full) nos avatares e badges.",  # noqa: E501
         "Border-radius: 12 cards, 24 CTA, 999 pills."),
        ("Spacing: usar multiples de 4. Padding interno cards 16px. Gap entre secoes 24px.",  # noqa: E501
         "Spacing scale 4-base. Card padding 16, section gap 24."),
        ("Icones: usar Lucide. Tamanho default 24px, exception ate 32px no bottom nav.",  # noqa: E501
         "Lucide icons. Default 24, bottom-nav 32."),
        ("Estados: hover, active, disabled. Hover scale 1.02. Active scale 0.98. Disabled opacity 0.5.",  # noqa: E501
         "Estados scale 1.02 hover / 0.98 active / 0.5 opacity disabled."),
        ("Animacoes: transition 200ms ease pra todos os estados.",
         "Transition 200ms ease."),
        ("Acessibilidade: contrast AA minimo. Botoes com aria-label. Focus ring visivel cyan.",  # noqa: E501
         "WCAG AA. aria-label nos botoes. Focus cyan."),
        ("Telas devem ser responsivas mas otimizadas pra mobile 375px width primary.",  # noqa: E501
         "Mobile-first 375px primary."),
        # filler — discussoes nao relacionadas pra forcar eviction
        ("Voltando ao Inicio: pensei se cabe um banner promocional no topo, tipo carrossel.",  # noqa: E501
         "Banner promo carrossel no Inicio pode entrar entre header e categorias."),
        ("Acho que nao precisa. Vamos focar nos elementos ja definidos. Pula o banner.",  # noqa: E501
         "OK sem banner. Foco no que ja foi definido."),
        ("Sobre estado vazio do carrito: mensagem 'Tu carrito esta vacio' + ilustracao + CTA voltar ao inicio.",  # noqa: E501
         "Empty state carrito: msg + ilustra + CTA."),
        ("Mas pra esse build vamos assumir carrinho com pelo menos 1 item. Fica simples.",  # noqa: E501
         "OK build com cart populado, skip empty state."),
        ("Loading states: skeleton screens com shimmer. Background cinza claro #F3F4F6.",  # noqa: E501
         "Skeleton screens com shimmer #F3F4F6."),
        ("Mas pra esse build estatico — nao precisa loading state. Markup final ja com dados.",  # noqa: E501
         "OK markup estatico com dados final."),
        ("Internacionalizacao: o app vai pra mercado LATAM, copy em espanhol.",
         "Copy es-LATAM."),
        ("Por isso 'Inicio', 'Carrito', 'Ordenar ahora', 'Realizar compra' em espanhol.",  # noqa: E501
         "Strings em espanhol confirmadas."),
        ("Preco em USD com 2 decimais. Symbol $ antes do numero. Sem espaco entre $ e digito.",  # noqa: E501
         "$ prefix sem espaco, 2 decimais."),
        ("Categorias do Inicio: Burgers, Pizza, Bebidas, Postres, Snacks.",
         "Categorias: Burgers / Pizza / Bebidas / Postres / Snacks."),
        ("Cards de Productos populares: 3-4 itens visiveis. Cada card tem foto + titulo + descricao curta + preco.",  # noqa: E501
         "Productos populares: 3-4 cards com foto + title + desc + preco."),
        ("Big Burger Queso aparece como item destacado tambem nos Productos populares.",  # noqa: E501
         "Big Burger Queso featured em productos populares tambem."),
        ("Recomendados: 3-4 sucos/smoothies. Cards verticais com BG pastel rosa #FCE7F3 ou amarelo #FEF3C7.",  # noqa: E501
         "Recomendados cards pastel rosa/amarelo."),
        ("Bottom nav background branco com border-top cinza claro. Icones cinza, ativo roxo primario.",  # noqa: E501
         "Bottom nav BG branco, border-top cinza, icon ativo roxo."),
        ("Para essa render: usa HTML semantico + Tailwind CSS via CDN. Inline styles ok onde Tailwind nao alcanca.",  # noqa: E501
         "Tailwind CDN + HTML semantico."),
        ("Renderiza as 3 telas lado a lado num container flex pra eu ver todas juntas.",  # noqa: E501
         "Layout flex horizontal das 3 telas."),
        ("Cada tela tem largura fixa 375px, altura 812px (iPhone reference).",
         "375x812 por tela."),
        ("Use imagens placeholder de https://placehold.co com cores brandeadas. Ex: https://placehold.co/200x200/7C3AED/white?text=Burger.",  # noqa: E501
         "placehold.co com cores brand."),
        ("Burger especial usa imagem grande: https://placehold.co/375x300/d97706/white?text=Hamburguesa+Especial.",  # noqa: E501
         "Hero burger 375x300 amber."),
        ("Para Big Burger Queso no carrito: https://placehold.co/80x80/7C3AED/white?text=Burger.",  # noqa: E501
         "Cart item thumb 80x80 roxo."),
    ]
    for u, a in decisoes:
        turns.append({"role": "user", "content": u})
        turns.append({"role": "assistant", "content": a})
    return turns


_FINAL_QUESTION = (
    "Agora gera o HTML completo das 3 telas lado a lado num container flex, "
    "baseado em TUDO que a gente discutiu na conversa. Output completo do "
    "<!DOCTYPE html> ate </html>, sem comentarios explicativos, soh o codigo. "
    "Lembra de aplicar fielmente: cores, tipografia, espacamentos, copy literal "
    "(em espanhol), precos exatos, layout das secoes, e estrutura do bottom nav."
)


def _check(text: str) -> tuple[list[str], list[str]]:
    hits = [a for a in DETAIL_ANCHORS if a.lower() in text.lower()]
    misses = [a for a in DETAIL_ANCHORS if a not in hits]
    return hits, misses


def _extract_text(resp: dict) -> str:
    return "".join(
        b.get("text", "")
        for b in resp.get("content", [])
        if isinstance(b, dict)
    )


def _call(
    base_url: str, auth: str, payload: dict, extra_headers: dict | None = None
) -> dict:
    headers = {
        "Authorization": auth,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    with httpx.Client(timeout=300.0) as client:
        r = client.post(f"{base_url}/v1/messages", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()


def run_vanilla_truncated(
    history: list[dict],
    base_url: str,
    auth: str,
    model: str,
    keep_last_n: int = 8,
) -> FrontendResult:
    kept = history[-keep_last_n:] + [{"role": "user", "content": _FINAL_QUESTION}]
    chars = sum(len(t["content"]) for t in kept)
    t0 = time.time()
    try:
        resp = _call(
            base_url,
            auth,
            {"model": model, "max_tokens": 8000, "messages": kept},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check(text)
        return FrontendResult(
            mode="vanilla_truncated",
            turns_sent=len(kept),
            chars_sent=chars,
            html_out=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            real_input_tokens=int(usage.get("input_tokens", 0)),
            anchors_hit=hits,
            anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return FrontendResult(
            mode="vanilla_truncated",
            turns_sent=len(kept),
            chars_sent=chars,
            html_out="",
            input_tokens=0,
            output_tokens=0,
            real_input_tokens=0,
            anchors_hit=[],
            anchors_missed=DETAIL_ANCHORS.copy(),
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def run_spillover_full(
    history: list[dict],
    proxy_base_url: str,
    auth: str,
    model: str,
) -> FrontendResult:
    full = history + [{"role": "user", "content": _FINAL_QUESTION}]
    chars = sum(len(t["content"]) for t in full)
    t0 = time.time()
    try:
        resp = _call(
            proxy_base_url,
            auth,
            {"model": model, "max_tokens": 8000, "messages": full},
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check(text)
        return FrontendResult(
            mode="spillover",
            turns_sent=len(full),
            chars_sent=chars,
            html_out=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            real_input_tokens=int(
                usage.get("spillover_real_input_tokens", usage.get("input_tokens", 0))
            ),
            anchors_hit=hits,
            anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return FrontendResult(
            mode="spillover",
            turns_sent=len(full),
            chars_sent=chars,
            html_out="",
            input_tokens=0,
            output_tokens=0,
            real_input_tokens=0,
            anchors_hit=[],
            anchors_missed=DETAIL_ANCHORS.copy(),
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def render_report(results: list[FrontendResult]) -> str:
    v = next((r for r in results if r.mode == "vanilla_truncated"), None)
    s = next((r for r in results if r.mode == "spillover"), None)

    def _hit(r):
        return f"{len(r.anchors_hit)}/{len(DETAIL_ANCHORS)}" if r else "-"

    lines = [
        "# Frontend A/B bench — app de delivery (3 telas)",
        "",
        f"Detalhes ancorados: {len(DETAIL_ANCHORS)}",
        f"Lista: {', '.join(DETAIL_ANCHORS)}",
        "",
        "## Resumo",
        "",
        "| metrica | vanilla_truncated | spillover |",
        "|---|---:|---:|",
        f"| detalhes citados | {_hit(v)} | {_hit(s)} |",
        f"| turnos enviados | {v.turns_sent if v else '-'} | {s.turns_sent if s else '-'} |",  # noqa: E501
        f"| chars enviados | {v.chars_sent if v else '-'} | {s.chars_sent if s else '-'} |",  # noqa: E501
        f"| input_tokens visivel | {v.input_tokens if v else '-'} | {s.input_tokens if s else '-'} |",  # noqa: E501
        f"| spillover_real_input_tokens | - | {s.real_input_tokens if s else '-'} |",
        f"| output_tokens | {v.output_tokens if v else '-'} | {s.output_tokens if s else '-'} |",  # noqa: E501
        f"| latencia ms | {v.latency_ms if v else '-'} | {s.latency_ms if s else '-'} |",  # noqa: E501
        f"| chars HTML output | {len(v.html_out) if v else 0} | {len(s.html_out) if s else 0} |",  # noqa: E501
        f"| erros | {1 if v and v.error else 0} | {1 if s and s.error else 0} |",
        "",
        "## Anchors por modo",
        "",
    ]
    for r in results:
        lines.append(f"### {r.mode}")
        lines.append(f"- hit: {', '.join(r.anchors_hit) or '(nenhum)'}")
        lines.append(f"- miss: {', '.join(r.anchors_missed) or '(nenhum)'}")
        if r.error:
            lines.append(f"- error: `{r.error}`")
        lines.append("")
    return "\n".join(lines) + "\n"
