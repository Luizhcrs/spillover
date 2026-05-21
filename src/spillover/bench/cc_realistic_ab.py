"""CC-realistic A/B bench — comparacao honesta vs Claude Code compaction real.

Bench anterior (`barbershop_ab`, `frontend_ab`) usou keep_last_n=8 (truncate).
Real Claude Code NAO trunca — ele resume via LLM. Comparacao injusta.

Aqui simulamos compaction REAL do CC:

  cc_realistic — multiplos compaction events:
    1. Conversa cresce ate threshold (configuravel)
    2. LLM call: 'summarize the conversation so far in <=400 tokens'
    3. Substitui turnos antigos pelo summary
    4. Continua. Pode repetir N vezes.
    5. Final question vai com [summary + tail + question]

  spillover — full historico via proxy ceiling=200:
    Eviction continua, archive raw, retrieve seletivo

Configuravel:
  --turns 500
  --compaction-threshold-turns 80  (a cada N turnos, vanilla compacta)
  --compaction-target-tokens 400  (alvo do summary)

Custo estimado: $0.50-1.00 por run (multiplos LLM calls no vanilla).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from spillover.bench.barbershop_ab import (
    DETAIL_ANCHORS as BARBER_ANCHORS,
)
from spillover.bench.barbershop_ab import (
    _build_history as _build_barber_history,
)
from spillover.bench.barbershop_ab import (
    _check as _check_barber,
)
from spillover.bench.barbershop_ab import (
    _extract_files as _extract_files_barber,
)


@dataclass
class CCRealisticResult:
    mode: str
    turns_input: int
    chars_input: int
    output: str
    files_extracted: dict[str, str]
    compaction_events: int  # quantos summaries foram chamados (vanilla)
    summary_calls_tokens: int  # total de tokens gastos em compaction
    input_tokens_final: int
    output_tokens_final: int
    real_input_tokens: int
    anchors_hit: list[str]
    anchors_missed: list[str]
    total_cost_usd_est: float
    latency_ms: int
    error: str | None = None


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


def _estimate_tokens(turns: list[dict]) -> int:
    """Estimativa char/4 do total de tokens de uma conversa."""
    return sum(len(t["content"]) for t in turns) // 4


def _summarize(
    base_url: str,
    auth: str,
    model: str,
    history_to_compress: list[dict],
    target_tokens: int = 400,
) -> tuple[str, dict]:
    """Chama Anthropic pra resumir trecho da conversa. Retorna (summary, usage)."""
    prompt = (
        f"You are compacting a long developer conversation to free up context. "
        f"Summarize the following {len(history_to_compress)} turns in at most "
        f"{target_tokens} tokens. Preserve all named decisions, file paths, "
        f"function names, exact numbers, prices, and identifiers. Drop only "
        f"conversational filler. Output the summary as a single paragraph, no "
        f"markdown, no preamble.\n\n"
        f"=== CONVERSATION TO COMPACT ===\n"
    )
    for t in history_to_compress:
        prompt += f"\n[{t['role']}] {t['content']}"
    prompt += "\n=== END ===\nSummary:"
    payload = {
        "model": model,
        "max_tokens": target_tokens + 100,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = _call(base_url, auth, payload)
    return _extract_text(resp), resp.get("usage", {})


def _build_long_history(turns: int = 500) -> list[dict]:
    """Estende a barbershop history ate N turnos com filler tematico."""
    base = _build_barber_history()  # ~226 turnos com 26 anchors
    if len(base) >= turns:
        return base[:turns]
    filler_templates = [
        ("Refletindo: deveriamos adicionar feature {i}?",
         "Anotado feature {i}. Decisao postponed pra v2."),
        ("Tarefa de housekeeping {i}: review do testing setup.",
         "Review {i} ok. Coverage atual aceitavel pra v1."),
        ("Code review iteracao {i}: imports, naming, formatting.",
         "Review {i}: tudo conforming. Sem flags."),
        ("Discussao sobre escolha do framework {i}: alternativas?",
         "Alternativas {i} avaliadas. Mantemos stack atual."),
        ("Refactor sugerido {i}: extrair logic comum.",
         "Refactor {i}: nice-to-have v2. Skip por enquanto."),
    ]
    i = 0
    while len(base) < turns:
        tpl = filler_templates[i % len(filler_templates)]
        base.append({"role": "user", "content": tpl[0].format(i=i)})
        base.append({"role": "assistant", "content": tpl[1].format(i=i)})
        i += 1
    return base[:turns]


_FINAL_QUESTION = (
    "Beleza, agora gera o projeto completo do sistema da barbearia baseado em TUDO "
    "que discutimos. Output em formato sequencial separando 3 arquivos:\n\n"
    "=== FILE: backend.py ===\n"
    "<codigo Python FastAPI completo: imports, schema SQL na boot, seed dos "
    "barbeiros+servicos, todos os endpoints REST, simulacao WhatsApp via log, "
    "GET / servindo frontend.html, CORS configurado>\n\n"
    "=== FILE: frontend.html ===\n"
    "<HTML completo single-page com Tailwind CDN, contendo as 5 telas em rotas hash, "
    "nav fixa, footer, todas as cores+fontes+layouts conforme spec, fetch API "
    "integrando com backend>\n\n"
    "=== FILE: schema.sql ===\n"
    "<DDL completo das 3 tabelas>\n\n"
    "Fidelidade ABSOLUTA aos nomes, precos, enderecos, cores, fontes, regras de "
    "negocio. Output dos 3 arquivos sem comentarios explicativos."
)


# precos por 1M tokens (input, output) USD
_MODEL_PRICING = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
    "claude-opus-4-6": (15.00, 75.00),
}


def _cost(input_tokens: int, output_tokens: int, model: str = "claude-haiku-4-5-20251001") -> float:
    in_price, out_price = _MODEL_PRICING.get(model, (1.00, 5.00))
    return (
        input_tokens * in_price / 1_000_000
        + output_tokens * out_price / 1_000_000
    )


def run_cc_realistic(
    history: list[dict],
    base_url: str,
    auth: str,
    model: str,
    compaction_threshold_turns: int = 80,
    target_summary_tokens: int = 400,
    keep_tail_turns: int = 20,
) -> CCRealisticResult:
    """Simula compaction real do Claude Code: a cada threshold turns, chama LLM
    pra sumarizar tudo exceto os ultimos keep_tail_turns, substitui pelo summary,
    continua. Repete ate processar history inteiro. Manda final question com
    [last summary + tail + question].
    """
    t0 = time.time()
    total_summary_in = 0
    total_summary_out = 0
    compaction_events = 0
    summary_text = ""

    cursor = 0
    while cursor + compaction_threshold_turns < len(history):
        chunk_end = cursor + compaction_threshold_turns
        chunk = history[cursor:chunk_end]
        if summary_text:
            # incluir summary anterior + chunk novo
            to_compact = (
                [{"role": "user", "content": f"[Earlier summary]\n{summary_text}"}]
                + chunk
            )
        else:
            to_compact = chunk
        try:
            summary_text, usage = _summarize(
                base_url, auth, model, to_compact, target_summary_tokens
            )
            total_summary_in += int(usage.get("input_tokens", 0))
            total_summary_out += int(usage.get("output_tokens", 0))
            compaction_events += 1
        except Exception as e:
            return CCRealisticResult(
                mode="cc_realistic",
                turns_input=len(history),
                chars_input=sum(len(t["content"]) for t in history),
                output="",
                files_extracted={},
                compaction_events=compaction_events,
                summary_calls_tokens=total_summary_in + total_summary_out,
                input_tokens_final=0,
                output_tokens_final=0,
                real_input_tokens=0,
                anchors_hit=[],
                anchors_missed=BARBER_ANCHORS.copy(),
                total_cost_usd_est=_cost(total_summary_in, total_summary_out, model),
                latency_ms=int((time.time() - t0) * 1000),
                error=f"compaction call failed: {e}",
            )
        cursor = chunk_end

    # tail = turnos apos ultimo ponto de compaction, ate o fim
    tail = history[cursor:]

    # Final messages: [summary as user-context] + tail + final question
    final_messages = []
    if summary_text:
        final_messages.append(
            {
                "role": "user",
                "content": (
                    "Continuing our long developer conversation. Summary of "
                    "what we discussed so far:\n\n" + summary_text
                ),
            }
        )
        final_messages.append(
            {
                "role": "assistant",
                "content": "Compreendi o contexto. Pode prosseguir.",
            }
        )
    final_messages.extend(tail)
    final_messages.append({"role": "user", "content": _FINAL_QUESTION})

    try:
        resp = _call(
            base_url,
            auth,
            {"model": model, "max_tokens": 16000, "messages": final_messages},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        in_t = int(usage.get("input_tokens", 0))
        out_t = int(usage.get("output_tokens", 0))
        hits, misses = _check_barber(text)
        return CCRealisticResult(
            mode="cc_realistic",
            turns_input=len(history),
            chars_input=sum(len(t["content"]) for t in history),
            output=text,
            files_extracted=_extract_files_barber(text),
            compaction_events=compaction_events,
            summary_calls_tokens=total_summary_in + total_summary_out,
            input_tokens_final=in_t,
            output_tokens_final=out_t,
            real_input_tokens=in_t,
            anchors_hit=hits,
            anchors_missed=misses,
            total_cost_usd_est=_cost(total_summary_in + in_t, total_summary_out + out_t, model),
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return CCRealisticResult(
            mode="cc_realistic",
            turns_input=len(history),
            chars_input=sum(len(t["content"]) for t in history),
            output="",
            files_extracted={},
            compaction_events=compaction_events,
            summary_calls_tokens=total_summary_in + total_summary_out,
            input_tokens_final=0,
            output_tokens_final=0,
            real_input_tokens=0,
            anchors_hit=[],
            anchors_missed=BARBER_ANCHORS.copy(),
            total_cost_usd_est=_cost(total_summary_in, total_summary_out, model),
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def run_spillover_full(
    history: list[dict],
    proxy_base_url: str,
    auth: str,
    model: str,
) -> CCRealisticResult:
    """Same history routed full through spillover proxy."""
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
        in_t = int(usage.get("input_tokens", 0))
        out_t = int(usage.get("output_tokens", 0))
        real_in = int(usage.get("spillover_real_input_tokens", in_t))
        hits, misses = _check_barber(text)
        return CCRealisticResult(
            mode="spillover",
            turns_input=len(history),
            chars_input=chars,
            output=text,
            files_extracted=_extract_files_barber(text),
            compaction_events=0,
            summary_calls_tokens=0,
            input_tokens_final=in_t,
            output_tokens_final=out_t,
            real_input_tokens=real_in,
            anchors_hit=hits,
            anchors_missed=misses,
            total_cost_usd_est=_cost(real_in, out_t, model),
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return CCRealisticResult(
            mode="spillover",
            turns_input=len(history),
            chars_input=chars,
            output="",
            files_extracted={},
            compaction_events=0,
            summary_calls_tokens=0,
            input_tokens_final=0,
            output_tokens_final=0,
            real_input_tokens=0,
            anchors_hit=[],
            anchors_missed=BARBER_ANCHORS.copy(),
            total_cost_usd_est=0.0,
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def render_report(results: list[CCRealisticResult]) -> str:
    v = next((r for r in results if r.mode == "cc_realistic"), None)
    s = next((r for r in results if r.mode == "spillover"), None)

    def _hit(r):
        return f"{len(r.anchors_hit)}/{len(BARBER_ANCHORS)}" if r else "-"

    def _files(r):
        return ", ".join(r.files_extracted.keys()) if r and r.files_extracted else "-"

    lines = [
        "# CC-realistic A/B — barbearia full system",
        "",
        f"Detalhes ancorados: {len(BARBER_ANCHORS)}",
        "",
        "## Resumo",
        "",
        "| metrica | cc_realistic (vanilla com compaction) | spillover |",
        "|---|---:|---:|",
        f"| detalhes citados | {_hit(v)} | {_hit(s)} |",
        f"| arquivos extraidos | {_files(v)} | {_files(s)} |",
        f"| turnos input | {v.turns_input if v else '-'} | {s.turns_input if s else '-'} |",  # noqa: E501
        f"| chars input | {v.chars_input if v else '-'} | {s.chars_input if s else '-'} |",  # noqa: E501
        f"| compaction events | {v.compaction_events if v else 0} | 0 |",
        f"| summary tokens gastos (vanilla) | {v.summary_calls_tokens if v else 0} | 0 |",  # noqa: E501
        f"| input_tokens final | {v.input_tokens_final if v else '-'} | {s.input_tokens_final if s else '-'} |",  # noqa: E501
        f"| spillover_real_input_tokens | - | {s.real_input_tokens if s else '-'} |",
        f"| output_tokens | {v.output_tokens_final if v else '-'} | {s.output_tokens_final if s else '-'} |",  # noqa: E501
        f"| custo USD estimado | ${v.total_cost_usd_est:.4f} | ${s.total_cost_usd_est:.4f} |"  # noqa: E501
        if (v and s)
        else "| custo USD | - | - |",
        f"| latency_ms | {v.latency_ms if v else '-'} | {s.latency_ms if s else '-'} |",  # noqa: E501
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
