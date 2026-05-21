"""Targeted Questions A/B — real use case: perguntas pontuais sobre detalhes antigos.

Bench anterior pediu "gera projeto inteiro" — omnibus que regurgita tudo.
Real uso: dev faz 1 pergunta especifica ("qual era o preco do combo?") apos
sessao longa. Spillover deve dominar aqui — retrieval surgico vs compaction
generica.

25 perguntas, cada uma pedindo 1 detalhe especifico que foi estabelecido em
algum turno especifico da conversa. Score = match de anchor.

CC: 1 sessao de compaction (cached) + 25 Q calls com [summary + tail + Q].
spillover: warmup eviction (1 call) + 25 Q calls usando archive accumulated.

Custo estimado: ~$0.20-0.50 cada modo.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from spillover.bench.barbershop_ab import (
    _build_history as _build_barber_history,
)
from spillover.bench.cc_realistic_ab import _summarize


@dataclass
class TargetedQuestion:
    id: str
    question: str
    expected_anchors: list[str]  # qualquer um dos anchors basta


@dataclass
class QResult:
    qid: str
    mode: str
    response: str
    hits: list[str]
    missed_all: bool
    input_tokens: int
    output_tokens: int
    real_input_tokens: int
    latency_ms: int
    cost_usd: float
    error: str | None = None


# 25 perguntas pontuais sobre turnos especificos da conversa barbershop
QUESTIONS: list[TargetedQuestion] = [
    TargetedQuestion("nome", "Qual e o nome da barbearia?",
                     ["Tres Tesouras"]),
    TargetedQuestion("endereco", "Qual o endereco da barbearia?",
                     ["Rua das Palmeiras 123", "Palmeiras 123"]),
    TargetedQuestion("telefone", "Qual o telefone de contato?",
                     ["(11) 98765-4321", "98765-4321"]),
    TargetedQuestion("horario", "Qual o horario de funcionamento?",
                     ["9h", "9 as 19", "9-19", "terca a sabado"]),
    TargetedQuestion("barbeiro_degrade",
                     "Quem e o barbeiro especialista em degrade?",
                     ["Joao"]),
    TargetedQuestion("barbeiro_barba",
                     "Quem e o barbeiro especialista em barba e bigode?",
                     ["Pedro"]),
    TargetedQuestion("barbeiro_jovem",
                     "Quem e o barbeiro mais novo (5 anos exp, cortes jovens)?",
                     ["Carlos"]),
    TargetedQuestion("preco_corte",
                     "Quanto custa o Corte Masculino?",
                     ["45", "R$ 45"]),
    TargetedQuestion("preco_barba",
                     "Quanto custa a Barba Completa?",
                     ["35", "R$ 35"]),
    TargetedQuestion("preco_combo",
                     "Quanto custa o Combo Cabelo + Barba?",
                     ["75", "R$ 75"]),
    TargetedQuestion("preco_pigmentacao",
                     "Quanto custa a pigmentacao de cabelo?",
                     ["90", "R$ 90"]),
    TargetedQuestion("preco_sobrancelha",
                     "Quanto custa o servico de sobrancelha masculina?",
                     ["20", "R$ 20"]),
    TargetedQuestion("duracao_combo",
                     "Quanto tempo dura o Combo Cabelo + Barba?",
                     ["60", "60 min", "60min", "uma hora", "1h"]),
    TargetedQuestion("slot_min",
                     "Qual o intervalo dos slots de agendamento?",
                     ["30 min", "30min", "30 minutos", "trinta"]),
    TargetedQuestion("cancelamento",
                     "Ate quanto tempo antes o cliente pode cancelar?",
                     ["1 hora", "uma hora", "1h"]),
    TargetedQuestion("confirma",
                     "Como o cliente recebe confirmacao do agendamento?",
                     ["WhatsApp"]),
    TargetedQuestion("stack",
                     "Qual o stack do backend?",
                     ["FastAPI", "SQLite"]),
    TargetedQuestion("orm_ou_sql",
                     "Usamos ORM ou SQL direto no backend?",
                     ["SQL direto", "sem ORM", "direct SQL"]),
    TargetedQuestion("status_default",
                     "Qual o valor default do campo status em agendamentos?",
                     ["pendente"]),
    TargetedQuestion("endpoint_listar_barbeiros",
                     "Qual o endpoint para listar barbeiros?",
                     ["GET /api/barbeiros", "/api/barbeiros"]),
    TargetedQuestion("endpoint_criar_agendamento",
                     "Qual o endpoint e metodo para criar agendamento?",
                     ["POST /api/agendamentos", "POST", "/api/agendamentos"]),
    TargetedQuestion("delete_regra",
                     "Qual a regra do endpoint DELETE em agendamento?",
                     ["403", "1 hora", "1h antes", "uma hora"]),
    TargetedQuestion("cor_primary",
                     "Qual cor primary do site?",
                     ["#1a1a1a", "1a1a1a", "preto"]),
    TargetedQuestion("cor_secondary",
                     "Qual cor secondary/dourado?",
                     ["#d4a574", "d4a574", "dourado"]),
    TargetedQuestion("cor_bg",
                     "Qual a cor de background do site?",
                     ["#f5f1ea", "f5f1ea", "creme"]),
    TargetedQuestion("font_titulo",
                     "Que fonte usa nos titulos?",
                     ["Playfair Display", "Playfair"]),
    TargetedQuestion("font_corpo",
                     "Que fonte usa no corpo do texto?",
                     ["Inter"]),
    TargetedQuestion("num_telas",
                     "Quantas telas tem o frontend?",
                     ["5", "cinco"]),
]


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


_MODEL_PRICING = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
}


def _cost(in_tok: int, out_tok: int, model: str) -> float:
    p_in, p_out = _MODEL_PRICING.get(model, (1.00, 5.00))
    return in_tok * p_in / 1_000_000 + out_tok * p_out / 1_000_000


def _check(text: str, expected: list[str]) -> tuple[list[str], bool]:
    hits = [e for e in expected if e.lower() in text.lower()]
    return hits, len(hits) == 0


# ============== CC realistic mode ==============

def build_cc_cached_state(
    history: list[dict],
    base_url: str,
    auth: str,
    model: str,
    compaction_threshold_turns: int = 80,
    target_summary_tokens: int = 500,
    keep_tail_turns: int = 30,
) -> tuple[list[dict], dict]:
    """Rola compaction sequencial pra produzir um cached state.

    Retorna (messages_pra_usar_em_questions, totals_dict).
    """
    total_in = 0
    total_out = 0
    n_compactions = 0
    summary_text = ""

    cursor = 0
    while cursor + compaction_threshold_turns < len(history):
        chunk_end = cursor + compaction_threshold_turns
        chunk = history[cursor:chunk_end]
        if summary_text:
            to_compact = (
                [{"role": "user", "content": f"[Earlier summary]\n{summary_text}"}]
                + chunk
            )
        else:
            to_compact = chunk
        summary_text, usage = _summarize(
            base_url, auth, model, to_compact, target_summary_tokens
        )
        total_in += int(usage.get("input_tokens", 0))
        total_out += int(usage.get("output_tokens", 0))
        n_compactions += 1
        cursor = chunk_end

    # Cached state messages = summary + tail
    tail = history[cursor:]
    cached_messages: list[dict] = []
    if summary_text:
        cached_messages.append(
            {
                "role": "user",
                "content": (
                    "Continuing our long developer conversation. Summary of "
                    "what we discussed so far:\n\n" + summary_text
                ),
            }
        )
        cached_messages.append(
            {
                "role": "assistant",
                "content": "Compreendi o contexto. Pode prosseguir.",
            }
        )
    cached_messages.extend(tail)

    totals = {
        "compaction_in": total_in,
        "compaction_out": total_out,
        "compactions": n_compactions,
        "summary_text_chars": len(summary_text),
    }
    return cached_messages, totals


def run_cc_question(
    cached_messages: list[dict],
    q: TargetedQuestion,
    base_url: str,
    auth: str,
    model: str,
) -> QResult:
    messages = cached_messages + [{"role": "user", "content": q.question}]
    t0 = time.time()
    try:
        resp = _call(
            base_url,
            auth,
            {"model": model, "max_tokens": 200, "messages": messages},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        in_t = int(usage.get("input_tokens", 0))
        out_t = int(usage.get("output_tokens", 0))
        hits, missed = _check(text, q.expected_anchors)
        return QResult(
            qid=q.id,
            mode="cc_cached",
            response=text,
            hits=hits,
            missed_all=missed,
            input_tokens=in_t,
            output_tokens=out_t,
            real_input_tokens=in_t,
            latency_ms=int((time.time() - t0) * 1000),
            cost_usd=_cost(in_t, out_t, model),
        )
    except Exception as e:
        return QResult(
            qid=q.id,
            mode="cc_cached",
            response="",
            hits=[],
            missed_all=True,
            input_tokens=0,
            output_tokens=0,
            real_input_tokens=0,
            latency_ms=int((time.time() - t0) * 1000),
            cost_usd=0.0,
            error=str(e),
        )


# ============== Spillover mode ==============

def spillover_warmup(
    history: list[dict],
    proxy_base_url: str,
    auth: str,
    model: str,
) -> dict:
    """Manda uma vez full historico pra popular archive. Retorna usage."""
    full = history + [
        {"role": "user", "content": "Apenas ok pra dizer que entendeu todo contexto."}
    ]
    resp = _call(
        proxy_base_url,
        auth,
        {"model": model, "max_tokens": 50, "messages": full},
        extra_headers={"anthropic-beta": "oauth-2025-04-20"},
    )
    return resp.get("usage", {})


def run_spillover_question(
    q: TargetedQuestion,
    proxy_base_url: str,
    auth: str,
    model: str,
) -> QResult:
    """Manda APENAS a pergunta. Retrieval do proxy puxa LTM dos archives."""
    messages = [{"role": "user", "content": q.question}]
    t0 = time.time()
    try:
        resp = _call(
            proxy_base_url,
            auth,
            {"model": model, "max_tokens": 200, "messages": messages},
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        in_t = int(usage.get("input_tokens", 0))
        out_t = int(usage.get("output_tokens", 0))
        real_in = int(usage.get("spillover_real_input_tokens", in_t))
        hits, missed = _check(text, q.expected_anchors)
        return QResult(
            qid=q.id,
            mode="spillover",
            response=text,
            hits=hits,
            missed_all=missed,
            input_tokens=in_t,
            output_tokens=out_t,
            real_input_tokens=real_in,
            latency_ms=int((time.time() - t0) * 1000),
            cost_usd=_cost(real_in, out_t, model),
        )
    except Exception as e:
        return QResult(
            qid=q.id,
            mode="spillover",
            response="",
            hits=[],
            missed_all=True,
            input_tokens=0,
            output_tokens=0,
            real_input_tokens=0,
            latency_ms=int((time.time() - t0) * 1000),
            cost_usd=0.0,
            error=str(e),
        )


def render_report(
    cc_results: list[QResult],
    sp_results: list[QResult],
    cc_compaction_cost: float,
    sp_warmup_cost: float,
    cc_compactions: int,
    cc_summary_chars: int,
) -> str:
    cc_hits = sum(1 for r in cc_results if not r.missed_all)
    sp_hits = sum(1 for r in sp_results if not r.missed_all)
    total_q = len(cc_results)

    cc_in = sum(r.input_tokens for r in cc_results)
    cc_out = sum(r.output_tokens for r in cc_results)
    sp_in = sum(r.input_tokens for r in sp_results)
    sp_out = sum(r.output_tokens for r in sp_results)
    sp_real = sum(r.real_input_tokens for r in sp_results)

    cc_q_cost = sum(r.cost_usd for r in cc_results)
    sp_q_cost = sum(r.cost_usd for r in sp_results)

    cc_total = cc_compaction_cost + cc_q_cost
    sp_total = sp_warmup_cost + sp_q_cost

    cc_lat = sum(r.latency_ms for r in cc_results) / max(1, total_q)
    sp_lat = sum(r.latency_ms for r in sp_results) / max(1, total_q)

    lines = [
        "# Targeted Questions A/B — perguntas pontuais",
        "",
        f"Total de perguntas: {total_q}",
        f"CC compaction events: {cc_compactions} (cached state)",
        f"CC summary text chars: {cc_summary_chars}",
        "",
        "## Resumo",
        "",
        "| metrica | cc_cached | spillover |",
        "|---|---:|---:|",
        f"| perguntas acertadas | **{cc_hits}/{total_q}** | **{sp_hits}/{total_q}** |",
        f"| recall % | {100*cc_hits/total_q:.0f}% | {100*sp_hits/total_q:.0f}% |",
        f"| input tokens total (Q only) | {cc_in} | {sp_in} (visivel) / {sp_real} (real) |",  # noqa: E501
        f"| output tokens total | {cc_out} | {sp_out} |",
        f"| custo setup (compaction/warmup) | ${cc_compaction_cost:.4f} | ${sp_warmup_cost:.4f} |",  # noqa: E501
        f"| custo Q (por pergunta) | ${cc_q_cost:.4f} | ${sp_q_cost:.4f} |",
        f"| **custo TOTAL** | **${cc_total:.4f}** | **${sp_total:.4f}** |",
        f"| latencia media (Q only) | {cc_lat:.0f}ms | {sp_lat:.0f}ms |",
        f"| erros | {sum(1 for r in cc_results if r.error)} | {sum(1 for r in sp_results if r.error)} |",  # noqa: E501
        "",
        "## Per-question",
        "",
        "| Q | cc hit | cc resp (preview) | sp hit | sp resp (preview) |",
        "|---|---|---|---|---|",
    ]
    for cc_r, sp_r in zip(cc_results, sp_results, strict=True):
        cc_h = "yes" if not cc_r.missed_all else "**NO**"
        sp_h = "yes" if not sp_r.missed_all else "**NO**"
        cc_p = (cc_r.response or "").replace("|", "/").replace("\n", " ")[:60]
        sp_p = (sp_r.response or "").replace("|", "/").replace("\n", " ")[:60]
        lines.append(f"| `{cc_r.qid}` | {cc_h} | {cc_p} | {sp_h} | {sp_p} |")
    return "\n".join(lines) + "\n"
