"""Barbershop A/B bench — sistema completo (backend FastAPI + frontend HTML).

Conversa de ~150 turnos estabelece:
  - identidade da barbearia (nome, endereco, telefone, horario)
  - 3 barbeiros nomeados (Joao, Pedro, Carlos)
  - 5 servicos com precos exatos
  - 3 tabelas SQL (barbeiros, servicos, agendamentos)
  - 4 endpoints REST especificos
  - paleta visual + fontes + copy
  - 5 telas frontend
  - regras de negocio (slots 30min, confirmacao via WhatsApp, cancelamento ate 1h antes)

Modelo gera output sequencial:
  === FILE: backend.py ===
  ...
  === FILE: frontend.html ===
  ...
  === FILE: schema.sql ===
  ...

Modos:
  vanilla_truncated — so ve N ultimas msgs
  spillover         — full historico via proxy ceiling=150
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass
class BarbershopResult:
    mode: str
    turns_sent: int
    chars_sent: int
    output: str
    files_extracted: dict[str, str]
    input_tokens: int
    output_tokens: int
    real_input_tokens: int
    anchors_hit: list[str]
    anchors_missed: list[str]
    latency_ms: int
    error: str | None = None


DETAIL_ANCHORS = [
    # identidade
    "Barbearia Tres Tesouras",
    "Rua das Palmeiras 123",
    "(11) 98765-4321",
    # barbeiros
    "Joao",
    "Pedro",
    "Carlos",
    # servicos + precos
    "Corte Masculino",
    "Barba Completa",
    "Combo Cabelo + Barba",
    "R$ 45",
    "R$ 35",
    "R$ 75",
    # endpoints
    "/api/barbeiros",
    "/api/servicos",
    "/api/agendamentos",
    # tabelas SQL
    "CREATE TABLE barbeiros",
    "CREATE TABLE servicos",
    "CREATE TABLE agendamentos",
    # paleta + fonte
    "#1a1a1a",
    "#d4a574",
    "Playfair Display",
    # regras
    "30 minutos",
    "WhatsApp",
    # arquivos esperados
    "backend.py",
    "frontend.html",
    "schema.sql",
]


def _build_history() -> list[dict]:
    """150 turnos de conversa estabelecendo identidade + arquitetura."""
    decisoes = [
        ("Vamos comecar projeto de sistema pra barbearia. Nome: Barbearia Tres Tesouras.",  # noqa: E501
         "Anotado: Barbearia Tres Tesouras."),
        ("Endereco: Rua das Palmeiras 123, Sao Paulo SP.",
         "Endereco fixado: Rua das Palmeiras 123 - Sao Paulo SP."),
        ("Telefone de contato pra clientes ligarem: (11) 98765-4321. Mesmo numero do WhatsApp.",  # noqa: E501
         "Tel/WhatsApp: (11) 98765-4321."),
        ("Horario funcionamento: terca a sabado, 9h as 19h. Domingo e segunda fechado.",
         "Funcionamento: ter-sab 9h-19h. Dom-seg fechado."),
        ("Tres barbeiros no time. Primeiro: Joao, 15 anos de experiencia, especialista em degrade.",  # noqa: E501
         "Barbeiro 1: Joao - 15 anos, especialista degrade."),
        ("Segundo barbeiro: Pedro, 8 anos, especialista em barba e bigode.",
         "Barbeiro 2: Pedro - 8 anos, barba/bigode."),
        ("Terceiro barbeiro: Carlos, 5 anos, especialista em cortes modernos jovens.",
         "Barbeiro 3: Carlos - 5 anos, cortes jovens."),
        ("Servico principal: Corte Masculino, preco R$ 45, duracao 30 minutos.",
         "Servico 1: Corte Masculino R$ 45, 30min."),
        ("Servico 2: Barba Completa (com toalha quente), preco R$ 35, duracao 30min.",
         "Servico 2: Barba Completa R$ 35, 30min."),
        ("Servico 3: Combo Cabelo + Barba, preco R$ 75, duracao 60min.",
         "Servico 3: Combo Cabelo+Barba R$ 75, 60min."),
        ("Servico 4: Pigmentacao de Cabelo, preco R$ 90, duracao 90min.",
         "Servico 4: Pigmentacao R$ 90, 90min."),
        ("Servico 5: Sobrancelha Masculina, preco R$ 20, duracao 15min.",
         "Servico 5: Sobrancelha R$ 20, 15min."),
        ("Agendamento sempre em slots de 30 minutos. Comeca 9h00, 9h30, 10h00, etc.",
         "Slots fixos 30min comecando 9h00."),
        ("Cliente pode cancelar agendamento ate 1 hora antes. Apos isso bloqueio.",
         "Cancelamento ate 1h antes."),
        ("Apos confirmar agendamento, sistema dispara mensagem WhatsApp pro cliente.",
         "Confirmacao via WhatsApp pos-agendamento."),
        ("Stack backend: Python + FastAPI + SQLite. Sem ORM, SQL direto.",
         "Backend: FastAPI + SQLite + SQL direto."),
        ("Schema: 3 tabelas principais. Primeira: CREATE TABLE barbeiros (id INTEGER PK, nome TEXT, experiencia INTEGER, especialidade TEXT).",  # noqa: E501
         "schema tabela 1: barbeiros(id, nome, experiencia, especialidade)."),
        ("Segunda tabela: CREATE TABLE servicos (id INTEGER PK, nome TEXT, preco REAL, duracao_min INTEGER).",  # noqa: E501
         "schema tabela 2: servicos(id, nome, preco, duracao_min)."),
        ("Terceira tabela: CREATE TABLE agendamentos (id INTEGER PK, barbeiro_id INTEGER FK, servico_id INTEGER FK, cliente_nome TEXT, cliente_telefone TEXT, data_hora TIMESTAMP, status TEXT DEFAULT 'pendente').",  # noqa: E501
         "schema tabela 3: agendamentos(id, FKs, cliente, data_hora, status)."),
        ("Endpoint REST 1: GET /api/barbeiros — lista todos os 3 barbeiros.",
         "GET /api/barbeiros lista."),
        ("Endpoint 2: GET /api/servicos — lista os 5 servicos.",
         "GET /api/servicos lista."),
        ("Endpoint 3: POST /api/agendamentos — cria agendamento. Body: barbeiro_id, servico_id, cliente_nome, cliente_telefone, data_hora.",  # noqa: E501
         "POST /api/agendamentos cria. Body com 5 campos."),
        ("Endpoint 4: DELETE /api/agendamentos/{id} — cancela agendamento. Retorna 403 se faltar menos de 1h.",  # noqa: E501
         "DELETE /api/agendamentos/{id} com bloqueio 1h."),
        ("Frontend: HTML estatico single-page com Tailwind via CDN. Fetch API pra falar com backend.",  # noqa: E501
         "Frontend HTML+Tailwind CDN+fetch."),
        ("Cor primaria do site: #1a1a1a (preto profundo). Cor secundaria: #d4a574 (dourado fosco).",  # noqa: E501
         "Cores: primary #1a1a1a + secondary #d4a574."),
        ("Background geral: #f5f1ea (creme). Textos em #1a1a1a sobre creme.",
         "BG #f5f1ea, texto #1a1a1a."),
        ("Fonte titulos: Playfair Display (serif elegante). Fonte corpo: Inter.",
         "Fonts: Playfair Display titulos + Inter corpo."),
        ("Layout do site: 5 telas. Tela 1 Home / Landing.",
         "Tela 1: Home/Landing."),
        ("Tela 2: Agendar. Form com selectors pra barbeiro, servico, data, horario.",  # noqa: E501
         "Tela 2: Agendar com form."),
        ("Tela 3: Confirmacao. Mostra resumo do agendamento + botao 'Confirmar'.",
         "Tela 3: Confirmacao."),
        ("Tela 4: Meus Agendamentos. Cliente digita telefone, ve lista de agendamentos pendentes.",  # noqa: E501
         "Tela 4: Meus Agendamentos (busca por tel)."),
        ("Tela 5: Sobre. Endereco, telefone, horario, foto da fachada.",
         "Tela 5: Sobre com info+foto."),
        ("Nav superior fixa em todas telas: logo + 5 links + botao 'Agendar agora' destaque.",  # noqa: E501
         "Nav fixa: logo + 5 links + CTA destaque."),
        ("Logo do site: texto 'Tres Tesouras' em Playfair Display + emoji tesoura.",
         "Logo: 'Tres Tesouras' Playfair + emoji."),
        ("Hero da home: heading 'O melhor corte da cidade' em fonte gigante 6xl.",  # noqa: E501
         "Hero h1: 'O melhor corte da cidade' 6xl."),
        ("Subheading hero: 'Tradicao desde 1995. Tres barbeiros experientes esperando voce.'",  # noqa: E501
         "Hero sub: 'Tradicao desde 1995...'."),
        ("CTA principal hero: botao 'Agendar agora' dourado #d4a574 com texto preto.",  # noqa: E501
         "CTA hero: 'Agendar agora' dourado."),
        ("Secao Servicos na home: grid 5 cards. Cada card: nome, preco em destaque, duracao, botao 'Agendar'.",  # noqa: E501
         "Secao Servicos: 5 cards grid."),
        ("Secao Barbeiros na home: 3 cards verticais. Cada card: foto, nome, experiencia, especialidade.",  # noqa: E501
         "Secao Barbeiros: 3 cards."),
        ("Footer: 3 colunas. Col1 contato (endereco, tel, whatsapp). Col2 horario funcionamento. Col3 redes sociais.",  # noqa: E501
         "Footer 3 cols: contato/horario/redes."),
        ("Border-radius padrao: 8px nos cards, 24px nos botoes.",
         "Border-radius 8 cards / 24 botoes."),
        ("Box-shadow nos cards: 0 4px 12px rgba(0,0,0,0.08).",
         "Shadow cards 0 4px 12px black 8%."),
        ("Hover dos cards: scale 1.02 + shadow mais forte. Transition 200ms.",
         "Card hover scale 1.02 + shadow up. Trans 200ms."),
        ("Mobile breakpoint: 768px. Abaixo, grid vira coluna unica.",
         "Mobile bp 768. Grid -> coluna."),
        ("Form de agendamento: 4 selects + 1 input texto + 1 input tel. Validacao client-side basic.",  # noqa: E501
         "Form: 4 selects + texto + tel + validate."),
        ("Backend pra confirmar agendamento simula envio WhatsApp via log. Mensagem: 'Olá {nome}, agendamento confirmado pra {data_hora}'.",  # noqa: E501
         "Confirma WhatsApp mock via log."),
        ("Cors backend: allow_origins=['*'] no dev. Producao restringir.",
         "Cors *, dev only."),
        ("Banco SQLite ficara em arquivo barbearia.db local. Schema rodado no boot.",  # noqa: E501
         "DB: barbearia.db local boot."),
        ("Boot do backend cria DB se nao existir + insere seed dos 3 barbeiros + 5 servicos.",  # noqa: E501
         "Boot: cria DB + seed."),
        ("Seed barbeiros: insere Joao/Pedro/Carlos com IDs 1,2,3.",
         "Seed barbeiros 1=Joao, 2=Pedro, 3=Carlos."),
        ("Seed servicos: insere os 5 servicos com IDs 1-5.",
         "Seed servicos 1-5."),
        ("Endpoint adicional: GET /api/agendamentos?telefone={tel} — lista agendamentos do cliente.",  # noqa: E501
         "GET /api/agendamentos?telefone filtra."),
        ("Backend serve frontend.html como static em GET /. Mesmo processo, sem servidor separado.",  # noqa: E501
         "GET / serve frontend single file."),
        ("Imagens: usar https://placehold.co com cores brand. Foto barbeiro placeholder 200x200/1a1a1a/d4a574?text=Joao.",  # noqa: E501
         "placehold.co com brand pra fotos."),
        ("Foto fachada na tela Sobre: https://placehold.co/800x400/1a1a1a/d4a574?text=Tres+Tesouras.",  # noqa: E501
         "Fachada 800x400 brand."),
        # filler — discussoes nao-anchor pra forcar eviction
        ("Voltando, deveriamos adicionar sistema de pontos fidelidade?",
         "Talvez v2. Por enquanto sem."),
        ("Pagamentos online? Stripe ou mercadopago?",
         "Pagamento presencial v1. Online v2."),
        ("Lembretes automaticos 1 dia antes do agendamento?",
         "v2 feature."),
        ("Multi-idioma EN/ES tambem?",
         "v1 so PT-BR."),
        ("Sistema de reviews dos clientes?",
         "v2."),
        ("Dashboard admin pra ver KPIs?",
         "v2."),
        ("Reset DB todos domingos via cron?",
         "Nao, manter dados."),
        ("Backup automatico SQLite?",
         "Manual v1. Cron v2."),
        ("Auth login admin?",
         "v1 sem auth, painel publico simples."),
        ("Notificacoes push?",
         "WhatsApp suficiente v1."),
        ("PWA mobile?",
         "v2."),
        ("Tema dark mode?",
         "Site ja e escuro #1a1a1a primary. Modo claro v2."),
        ("Hospedar onde? Vercel ou Railway?",
         "v1 local, deploy depois."),
        ("Dominio comprado?",
         "v1 sem dominio."),
        ("Logo profissional ou placeholder?",
         "Placeholder texto+emoji v1. Designer v2."),
        ("Tipografia escolhida tem boa renderizacao mobile?",
         "Playfair + Inter validados mobile."),
        ("Animacoes mais elaboradas?",
         "Hover scale + transition 200ms basico. Sem GSAP/Framer."),
        ("Loading spinners?",
         "Texto 'Carregando...' simples v1."),
        ("Error states?",
         "Alert nativo browser v1. Toast v2."),
        ("404 page?",
         "Redirect home v1."),
        ("SEO basico?",
         "Meta tags titulo+description v1."),
        ("Analytics?",
         "v2."),
        ("Cookie consent?",
         "Sem cookies v1."),
        ("Sitemap?",
         "v2."),
        ("Schema.org structured data?",
         "v2."),
        ("Open Graph tags?",
         "v2."),
        ("Favicon?",
         "Emoji tesoura inline v1."),
        ("Manifest PWA?",
         "v2."),
        ("Service worker offline?",
         "v2."),
        ("CDN pra assets?",
         "Tailwind ja via CDN. Resto static."),
        ("Testes automatizados backend?",
         "Pytest basico v1."),
        ("Testes E2E?",
         "v2 Playwright."),
        ("Linting?",
         "ruff Python."),
        ("Formatter?",
         "ruff format."),
        ("Pre-commit hooks?",
         "v1 manual. Husky/pre-commit v2."),
        ("CI/CD?",
         "v2 GitHub Actions."),
        ("Docker?",
         "v2 docker-compose."),
        ("Logs estruturados?",
         "v1 print/logging stdlib. JSON logs v2."),
        ("Metricas Prometheus?",
         "v2."),
        ("Tracing?",
         "v2."),
        ("Rate limit?",
         "v2."),
        ("CSRF protection?",
         "v1 simple form. v2 token."),
        ("HTTPS forced?",
         "v1 dev http. Prod always https."),
        ("Headers security CSP/HSTS?",
         "v2."),
        ("DB migrations?",
         "v1 schema direto. Alembic v2."),
        ("Connection pooling?",
         "v1 SQLite single connection. Postgres v2."),
        ("Async DB?",
         "v1 sync SQLite. aiosqlite v2."),
        ("WebSockets pra notificacoes real-time?",
         "v2."),
        ("File upload (avatar barbeiro)?",
         "v1 placeholder URL. Upload v2."),
        ("Pesquisa de servicos?",
         "v1 lista fixa. Search v2."),
        ("Filtros barbeiros?",
         "v1 lista fixa. Filtros v2."),
        ("Calendar view agendamentos?",
         "v1 lista. Calendar v2."),
        ("Drag-and-drop reagendar?",
         "v2."),
        ("Mobile app nativo?",
         "v2."),
        ("API publica documentada?",
         "v1 docstrings. OpenAPI/Swagger v2."),
        ("Versionamento API?",
         "v1 sem version. /v1 prefix v2."),
        ("Rate plans?",
         "v1 publico free. Pricing v2 SaaS."),
        ("Multi-tenancy?",
         "v1 single barbearia."),
    ]
    turns: list[dict] = []
    for u, a in decisoes:
        turns.append({"role": "user", "content": u})
        turns.append({"role": "assistant", "content": a})
    return turns


_FINAL_QUESTION = (
    "Beleza, agora gera o projeto completo do sistema baseado em TUDO que discutimos. "
    "Output em formato sequencial separando 3 arquivos:\n\n"
    "=== FILE: backend.py ===\n"
    "<codigo Python FastAPI completo: imports, schema SQL na boot, seed dos barbeiros+servicos, "
    "todos os endpoints REST, simulacao WhatsApp via log, GET / servindo frontend.html, "
    "CORS configurado>\n\n"
    "=== FILE: frontend.html ===\n"
    "<HTML completo single-page com Tailwind CDN, contendo as 5 telas em rotas hash "
    "(#home, #agendar, #confirmacao, #meus-agendamentos, #sobre), nav fixa, footer, "
    "todas as cores+fontes+layouts conforme spec, fetch API integrando com backend>\n\n"
    "=== FILE: schema.sql ===\n"
    "<DDL completo das 3 tabelas>\n\n"
    "Mantenha fidelidade absoluta aos detalhes ja estabelecidos: nomes, precos, enderecos, "
    "cores, fontes, regras de negocio. Output dos 3 arquivos sem texto explicativo antes/depois, "
    "soh as marcacoes === FILE === separando."
)


def _extract_files(text: str) -> dict[str, str]:
    """Extrai blocos === FILE: nome === do output."""
    import re

    files: dict[str, str] = {}
    matches = list(re.finditer(r"===\s*FILE:\s*([^\s=]+)\s*===", text))
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        # remove fenced code if present
        if chunk.startswith("```"):
            chunk = re.sub(r"^```[a-z]*\n", "", chunk, count=1)
            chunk = re.sub(r"\n```\s*$", "", chunk, count=1)
        files[name] = chunk
    return files


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
    with httpx.Client(timeout=600.0) as client:
        r = client.post(f"{base_url}/v1/messages", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()


def run_vanilla_truncated(
    history: list[dict],
    base_url: str,
    auth: str,
    model: str,
    keep_last_n: int = 8,
) -> BarbershopResult:
    kept = history[-keep_last_n:] + [{"role": "user", "content": _FINAL_QUESTION}]
    chars = sum(len(t["content"]) for t in kept)
    t0 = time.time()
    try:
        resp = _call(
            base_url,
            auth,
            {"model": model, "max_tokens": 16000, "messages": kept},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check(text)
        return BarbershopResult(
            mode="vanilla_truncated",
            turns_sent=len(kept),
            chars_sent=chars,
            output=text,
            files_extracted=_extract_files(text),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            real_input_tokens=int(usage.get("input_tokens", 0)),
            anchors_hit=hits,
            anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return BarbershopResult(
            mode="vanilla_truncated",
            turns_sent=len(kept),
            chars_sent=chars,
            output="",
            files_extracted={},
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
) -> BarbershopResult:
    full = history + [{"role": "user", "content": _FINAL_QUESTION}]
    chars = sum(len(t["content"]) for t in full)
    t0 = time.time()
    try:
        resp = _call(
            proxy_base_url,
            auth,
            {"model": model, "max_tokens": 16000, "messages": full},
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check(text)
        return BarbershopResult(
            mode="spillover",
            turns_sent=len(full),
            chars_sent=chars,
            output=text,
            files_extracted=_extract_files(text),
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
        return BarbershopResult(
            mode="spillover",
            turns_sent=len(full),
            chars_sent=chars,
            output="",
            files_extracted={},
            input_tokens=0,
            output_tokens=0,
            real_input_tokens=0,
            anchors_hit=[],
            anchors_missed=DETAIL_ANCHORS.copy(),
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def render_report(results: list[BarbershopResult]) -> str:
    v = next((r for r in results if r.mode == "vanilla_truncated"), None)
    s = next((r for r in results if r.mode == "spillover"), None)

    def _hit(r):
        return f"{len(r.anchors_hit)}/{len(DETAIL_ANCHORS)}" if r else "-"

    def _files(r):
        if not r:
            return "-"
        return ", ".join(r.files_extracted.keys()) or "(nenhum)"

    lines = [
        "# Barbearia A/B bench — sistema completo backend+frontend",
        "",
        f"Detalhes ancorados: {len(DETAIL_ANCHORS)}",
        "",
        "## Resumo",
        "",
        "| metrica | vanilla_truncated | spillover |",
        "|---|---:|---:|",
        f"| detalhes citados | {_hit(v)} | {_hit(s)} |",
        f"| arquivos extraidos | {_files(v)} | {_files(s)} |",
        f"| turnos enviados | {v.turns_sent if v else '-'} | {s.turns_sent if s else '-'} |",  # noqa: E501
        f"| chars enviados | {v.chars_sent if v else '-'} | {s.chars_sent if s else '-'} |",  # noqa: E501
        f"| input_tokens visivel | {v.input_tokens if v else '-'} | {s.input_tokens if s else '-'} |",  # noqa: E501
        f"| spillover_real_input_tokens | - | {s.real_input_tokens if s else '-'} |",
        f"| output_tokens | {v.output_tokens if v else '-'} | {s.output_tokens if s else '-'} |",  # noqa: E501
        f"| latency_ms | {v.latency_ms if v else '-'} | {s.latency_ms if s else '-'} |",  # noqa: E501
        f"| chars output | {len(v.output) if v else 0} | {len(s.output) if s else 0} |",  # noqa: E501
        f"| erros | {1 if v and v.error else 0} | {1 if s and s.error else 0} |",
        "",
    ]
    for r in results:
        lines.append(f"## {r.mode}")
        lines.append(f"- hit: {', '.join(r.anchors_hit) or '(nenhum)'}")
        lines.append(f"- miss: {', '.join(r.anchors_missed) or '(nenhum)'}")
        if r.error:
            lines.append(f"- error: `{r.error}`")
        lines.append("")
    return "\n".join(lines) + "\n"
