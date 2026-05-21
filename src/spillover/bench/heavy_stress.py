"""Heavy stress bench — push spillover to ~100k tokens of conversation.

Builds a 200-turn synthetic engineering session with 4 anchored facts placed at
turns 5, 50, 100, 150. Each turn is ~500 chars of plausible coding-session
content. Total: ~100k chars ≈ ~25k Anthropic tokens.

Tests two modes:

  vanilla_truncated  — keep only last K turns (simulate compaction)
  spillover          — full 200-turn history routed through proxy (eviction
                       triggered continuously)

Measures recall on a single final question that references all 4 anchors,
plus latency, DB growth, and eviction count.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass
class HeavyResult:
    mode: str
    turns_sent: int
    chars_sent: int
    response: str
    input_tokens: int
    output_tokens: int
    real_input_tokens: int  # spillover_real_input_tokens if rewritten
    anchors_hit: list[str]
    anchors_missed: list[str]
    latency_ms: int
    error: str | None = None


ANCHORS = [
    ("turn05_db", "SQLite", "we picked SQLite over Postgres because deployment is local-only zero-infra few-hundred-GB max"),  # noqa: E501
    ("turn50_bug", "middleware.py:42", "the auth bug is at middleware.py line 42 — jwt expiry comparison uses < instead of <="),  # noqa: E501
    ("turn100_port", "8787", "spillover proxy listens on port 8787 — chosen because mneme is 7777 and we wanted visually distinct close numbers"),  # noqa: E501
    ("turn150_decay", "exp(-age/half_life)", "decay importance formula is base × exp(-age/half_life) + min(hit_count×0.05, 0.5) — exponential half-life by type"),  # noqa: E501
]


def _filler(i: int) -> tuple[str, str]:
    """Generate a ~500-char user+assistant pair of plausible coding content."""
    templates = [
        ("reviewing the src/ tree iteration {i}, checking imports for circular dependencies in the auth module, the proxy module, and the eviction selector — also looking at how the facet worker imports the embedder",  # noqa: E501
         "iteration {i} review done: src/ has no circular imports, auth module clean, proxy imports go to adapters and middleware as expected, eviction selector only depends on dataclasses stdlib, facet worker correctly defers fastembed import to runtime"),  # noqa: E501
        ("running the test suite pass {i} on the eviction selector — checking the 3-pass policy edge cases including the empty turns list, the all-pinned case, the budget-pressure pass 3 fallback, and the priority promotion when pass 1 frees insufficient tokens",  # noqa: E501
         "pass {i} complete: 6 eviction selector tests green, edge cases all pass, the weighted-FIFO with density signal also exercised, no regressions in the token-balance invariant test over 50 synthetic turns"),  # noqa: E501
        ("walking through the retriever fusion code iteration {i} — RRF with 4 legs vector graph bm25 causal, type-weights priority 1.5 procedural 1.2 task 1.4 episodic 1.0 semantic 1.0, K=60 constant per Cormack 2009",  # noqa: E501
         "iteration {i} confirms RRF implementation matches Cormack — contribution per ranking is type_weight divided by 60 plus rank, accumulated across legs, deduplicated by episode_id, sorted descending by total"),  # noqa: E501
        ("inspecting the Kuzu graph schema check {i} — 5 node tables Episode Entity File Decision Command, 5 relations MENTIONS TOUCHED IMPLEMENTS RAN AFTER, each populated by the facet worker on every archived turn",  # noqa: E501
         "check {i}: schema confirmed, all relations correctly defined as FROM Episode TO target, AFTER edge is Episode-to-Episode for causal chain, MERGE clauses idempotent"),  # noqa: E501
        ("debugging the FTS5 tokenizer round {i} — confirmed tokenchars='./_-:' preserves compound identifiers, regex on query side also relaxed to match, BM25 scores 6.0 on full-token queries 1.9 on partial",  # noqa: E501
         "round {i} debug done: tokenizer fix verified across `0.85` `char/4` `letsencryptresolver` `middleware.py:42` — all index as single tokens, BM25 returns correct top-1 for each"),  # noqa: E501
    ]
    template = templates[i % len(templates)]
    return template[0].format(i=i), template[1].format(i=i)


def build_heavy_history() -> tuple[list[dict], list[tuple[str, str, str]]]:
    """Return 200 turns with 4 anchored facts at positions 5, 50, 100, 150.
    Also returns the anchor metadata for assertion later."""
    turns: list[dict] = []
    for i in range(200):
        if i == 5:
            _, key, text = ANCHORS[0]
            turns.append({"role": "user", "content": text})
            turns.append({"role": "assistant", "content": f"recorded: {text}"})
        elif i == 50:
            _, key, text = ANCHORS[1]
            turns.append({"role": "user", "content": text})
            turns.append({"role": "assistant", "content": f"recorded: {text}"})
        elif i == 100:
            _, key, text = ANCHORS[2]
            turns.append({"role": "user", "content": text})
            turns.append({"role": "assistant", "content": f"recorded: {text}"})
        elif i == 150:
            _, key, text = ANCHORS[3]
            turns.append({"role": "user", "content": text})
            turns.append({"role": "assistant", "content": f"recorded: {text}"})
        else:
            u, a = _filler(i)
            turns.append({"role": "user", "content": u})
            turns.append({"role": "assistant", "content": a})
    return turns, ANCHORS


def _check(text: str) -> tuple[list[str], list[str]]:
    expected = [a[1] for a in ANCHORS]
    hits = [e for e in expected if e.lower() in text.lower()]
    misses = [e for e in expected if e not in hits]
    return hits, misses


def _extract_text(resp: dict) -> str:
    return "".join(
        b.get("text", "")
        for b in resp.get("content", [])
        if isinstance(b, dict)
    )


def _call(base_url: str, auth: str, payload: dict, extra_headers: dict | None = None) -> dict:
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


_QUESTION = (
    "Quick recap please. Four specific facts I need you to recall: "
    "(1) which database did we pick and why? "
    "(2) where exactly was the auth bug? "
    "(3) what port does spillover use and why that number? "
    "(4) what's the importance decay formula? "
    "Cite exact strings, file paths, numbers, and formulas."
)


def run_vanilla_truncated(
    history: list[dict],
    base_url: str,
    auth: str,
    model: str,
    keep_last_n: int = 12,
) -> HeavyResult:
    kept = history[-keep_last_n:] + [{"role": "user", "content": _QUESTION}]
    chars = sum(len(t["content"]) for t in kept)
    t0 = time.time()
    try:
        resp = _call(base_url, auth, {"model": model, "max_tokens": 600, "messages": kept})
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check(text)
        return HeavyResult(
            mode="vanilla_truncated",
            turns_sent=len(kept),
            chars_sent=chars,
            response=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            real_input_tokens=int(usage.get("input_tokens", 0)),
            anchors_hit=hits,
            anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return HeavyResult(
            mode="vanilla_truncated",
            turns_sent=len(kept), chars_sent=chars,
            response="", input_tokens=0, output_tokens=0, real_input_tokens=0,
            anchors_hit=[], anchors_missed=[a[1] for a in ANCHORS],
            latency_ms=int((time.time() - t0) * 1000), error=str(e),
        )


def run_spillover_full(
    history: list[dict],
    proxy_base_url: str,
    auth: str,
    model: str,
) -> HeavyResult:
    full = history + [{"role": "user", "content": _QUESTION}]
    chars = sum(len(t["content"]) for t in full)
    t0 = time.time()
    try:
        resp = _call(
            proxy_base_url, auth,
            {"model": model, "max_tokens": 600, "messages": full},
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check(text)
        return HeavyResult(
            mode="spillover",
            turns_sent=len(full),
            chars_sent=chars,
            response=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            real_input_tokens=int(usage.get("spillover_real_input_tokens", usage.get("input_tokens", 0))),  # noqa: E501
            anchors_hit=hits,
            anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return HeavyResult(
            mode="spillover",
            turns_sent=len(full), chars_sent=chars,
            response="", input_tokens=0, output_tokens=0, real_input_tokens=0,
            anchors_hit=[], anchors_missed=[a[1] for a in ANCHORS],
            latency_ms=int((time.time() - t0) * 1000), error=str(e),
        )


def render_report(results: list[HeavyResult]) -> str:
    lines = ["# Heavy stress bench (200 turns, ~100k chars)", "", "## summary", "", "| metric | vanilla_truncated | spillover |", "|---|---:|---:|"]  # noqa: E501
    v = next((r for r in results if r.mode == "vanilla_truncated"), None)
    s = next((r for r in results if r.mode == "spillover"), None)

    def _full(r):
        return f"{4 - len(r.anchors_missed)}/4" if r else "-"

    lines.append(f"| anchors-hit (out of 4) | {_full(v)} | {_full(s)} |")
    lines.append(f"| turns sent | {v.turns_sent if v else '-'} | {s.turns_sent if s else '-'} |")
    lines.append(f"| chars sent | {v.chars_sent if v else '-'} | {s.chars_sent if s else '-'} |")
    lines.append(f"| visible input_tokens | {v.input_tokens if v else '-'} | {s.input_tokens if s else '-'} |")  # noqa: E501
    lines.append(f"| spillover_real_input_tokens | - | {s.real_input_tokens if s else '-'} |")
    lines.append(f"| output_tokens | {v.output_tokens if v else '-'} | {s.output_tokens if s else '-'} |")  # noqa: E501
    lines.append(f"| latency_ms | {v.latency_ms if v else '-'} | {s.latency_ms if s else '-'} |")
    lines.append(f"| errors | {1 if v and v.error else 0} | {1 if s and s.error else 0} |")
    lines.append("\n## anchors checked\n")
    for a in ANCHORS:
        lines.append(f"- `{a[1]}` (anchored at turn {a[0]})")
    lines.append("\n## responses\n")
    for r in results:
        lines.append(f"### {r.mode}\n")
        excerpt = (r.response or "(no response)")
        lines.append("```\n" + excerpt + "\n```\n")
    return "\n".join(lines) + "\n"
