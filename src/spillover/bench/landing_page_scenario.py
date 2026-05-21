from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass
class DetailCheck:
    name: str
    question: str
    expected: list[str]    # any one of these tokens must appear


@dataclass
class LogicResult:
    detail: str
    mode: str
    response: str
    hits: list[str]
    missed_all: bool
    input_tokens: int
    output_tokens: int
    latency_ms: int
    error: str | None = None


LANDING_PAGE_DETAILS: list[DetailCheck] = [
    DetailCheck(  # noqa: E501
        "primary_cta",
        "What's the primary CTA text we agreed on?",
        ["Stop compacting", "Start spilling over"],
    ),
    DetailCheck(
        "accent_hex",
        "What hex color did we pick for accent?",
        ["#06FFB0", "06FFB0", "06ffb0"],
    ),
    DetailCheck("body_font", "What font did we pick for body copy?", ["Inter"]),
    DetailCheck("heading_font", "What font is the heading?", ["Geist Mono", "Geist"]),
    DetailCheck(
        "hero_headline",
        "What was the hero headline?",
        ["Agents never forget", "spill over"],
    ),
    DetailCheck("section_count", "How many sections total?", ["five", "5"]),
    DetailCheck(
        "pricing_cta",
        "What does the pricing-section CTA say?",
        ["Get the proxy"],
    ),
    DetailCheck(
        "email_placeholder",
        "What's the placeholder text in the email field?",
        ["work@yourcompany.com"],
    ),
    DetailCheck("footer_year", "What year is in the footer?", ["2026"]),
]


def build_landing_page_history() -> list[dict]:
    """Build a ~50-turn conversation that establishes every detail above."""
    turns: list[dict] = []

    # Establish each decision across the first ~30 turns. Mix in unrelated chatter.
    decisions = [
        (
            "Let's draft the primary CTA. I'm leaning toward "
            "'Stop compacting. Start spilling over.' — direct, punchy, names the problem.",
            "Agreed. Primary CTA: 'Stop compacting. Start spilling over.' "
            "It anchors the architectural opposition. Final.",
        ),
        (
            "Accent color — I want it to feel technical but alive. "
            "Mint-cyan in the #06FFB0 range.",
            "Locked in: accent #06FFB0. High-contrast on dark, accessible on light. "
            "Use for CTAs and active state highlights.",
        ),
        (
            "Body font: clean sans, modern. Inter has the right weight scale.",
            "Body font: Inter. Variable weight, good at small sizes. Decision recorded.",
        ),
        (
            "Heading font should contrast — mono feels right for a developer audience. "
            "Geist Mono.",
            "Heading font: Geist Mono. Pairs cleanly with Inter body. Decision final.",
        ),
        (
            "Hero headline: 'Agents never forget — they spill over.' "
            "Keeps the slogan but expands it.",
            "Hero headline locked: 'Agents never forget — they spill over.'",
        ),
        (
            "Section count: I want exactly 5. Hero, How it works, Demo, Pricing, "
            "Footer-CTA. No more.",
            "Five sections: Hero / How it works / Demo / Pricing / Footer-CTA. Tight.",
        ),
        (
            "Pricing-section CTA distinct from hero: 'Get the proxy'. "
            "Direct, no marketing fluff.",
            "Pricing CTA: 'Get the proxy'. Final.",
        ),
        (
            "Email signup placeholder: 'work@yourcompany.com'. "
            "Implies team usage, not personal.",
            "Email placeholder: 'work@yourcompany.com'.",
        ),
        (
            "Footer year: just 2026. No 'copyright', no '©'. Minimal.",
            "Footer year: 2026.",
        ),
    ]
    for u, a in decisions:
        turns.append({"role": "user", "content": u})
        turns.append({"role": "assistant", "content": a})

    # 30 turns of unrelated work to push the decisions out
    for i in range(15):
        turns.append({
            "role": "user",
            "content": f"sub-task {i:02d}: review the test suite, find slow tests",
        })
        turns.append({
            "role": "assistant",
            "content": (
                f"reviewed batch {i:02d} of tests, "
                "two slow ones flagged in tests/integration/"
            ),
        })
    return turns


def _check_any(text: str, expected: list[str]) -> tuple[list[str], bool]:
    hits = [e for e in expected if e.lower() in text.lower()]
    return hits, len(hits) == 0


def _extract_text(resp: dict) -> str:
    return "".join(
        b.get("text", "")
        for b in resp.get("content", [])
        if isinstance(b, dict)
    )


def _call(
    base_url: str,
    auth: str,
    payload: dict,
    extra_headers: dict | None = None,
) -> dict:
    headers = {
        "Authorization": auth,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    with httpx.Client(timeout=120.0) as client:
        r = client.post(f"{base_url}/v1/messages", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()


def run_logic_check(
    history: list[dict],
    detail: DetailCheck,
    base_url: str,
    auth: str,
    model: str,
    mode: str,
    extra_headers: dict | None = None,
) -> LogicResult:
    payload = {
        "model": model,
        "max_tokens": 100,
        "messages": history + [{"role": "user", "content": detail.question}],
    }
    t0 = time.time()
    try:
        resp = _call(base_url, auth, payload, extra_headers=extra_headers)
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, missed_all = _check_any(text, detail.expected)
        return LogicResult(
            detail=detail.name, mode=mode, response=text,
            hits=hits, missed_all=missed_all,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return LogicResult(
            detail=detail.name, mode=mode, response="",
            hits=[], missed_all=True,
            input_tokens=0, output_tokens=0,
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def render_logic_report(results: list[LogicResult]) -> str:
    by_mode: dict[str, list[LogicResult]] = {}
    for r in results:
        by_mode.setdefault(r.mode, []).append(r)
    lines = ["# Landing-page logic retention", ""]
    for mode, rs in by_mode.items():
        kept = sum(1 for r in rs if not r.missed_all)
        lines.append(f"- **{mode}**: {kept}/{len(rs)} details preserved")
    lines.append("\n## per-detail\n")
    lines.append("| detail | mode | hit | missed | response |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        hit = ",".join(r.hits) if r.hits else "-"
        missed = "yes" if r.missed_all else "no"
        excerpt = (r.response or "").replace("|", "/").replace("\n", " ")[:80]
        lines.append(f"| {r.detail} | {r.mode} | {hit} | {missed} | {excerpt} |")
    return "\n".join(lines) + "\n"
