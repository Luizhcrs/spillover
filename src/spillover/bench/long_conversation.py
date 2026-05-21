from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx


@dataclass
class LongConvResult:
    scenario_id: str
    mode: str  # "vanilla_truncated" | "spillover"
    response: str
    input_tokens: int
    output_tokens: int
    anchors_hit: list[str]
    anchors_missed: list[str]
    latency_ms: int
    error: str | None = None


@dataclass
class LongConvScenario:
    id: str
    pre_turns: list[dict]   # background work, no anchors
    anchor_turns: list[dict]  # turns containing the facts we want to recover
    filler_turns: list[dict]  # post-anchor work that pushes anchors out
    question: str
    expected_anchors: list[str]


def make_scenario_db_choice() -> LongConvScenario:
    """Scenario: 80 turns of work, db choice fact at turn 5, asked at end."""
    pre = []
    anchor = [
        {"role": "user", "content": "We're at the storage layer decision point. Local-only deployment, max few hundred GB, single user. Postgres or SQLite?"},
        {"role": "assistant", "content": "Given local-only + few hundred GB ceiling + zero-infra requirement, SQLite is the right call. Postgres adds a daemon + auth + connection pooling we don't need. Decision: SQLite. Reason: local-only + zero-infra + scale-fits."},
    ]
    filler = []
    # Fill to 80 turns total
    for i in range(38):
        pre.append({"role": "user", "content": f"sub-task {i:02d}: list files in src/, check imports, look for circular dependencies"})
        pre.append({"role": "assistant", "content": f"checked src/ at iteration {i:02d}, no circular imports found, structure clean"})
    for i in range(20):
        filler.append({"role": "user", "content": f"unrelated work {i:02d}: refactor the http client to use httpx instead of requests"})
        filler.append({"role": "assistant", "content": f"refactor pass {i:02d} done, httpx 0.27 wired, sync calls migrated"})
    return LongConvScenario(
        id="db_choice_long",
        pre_turns=pre,
        anchor_turns=anchor,
        filler_turns=filler,
        question="Remind me which database we picked and why exactly.",
        expected_anchors=["SQLite", "local"],
    )


def make_scenario_auth_bug() -> LongConvScenario:
    pre = []
    anchor = [
        {"role": "user", "content": "Bug in auth: tokens that expire AT THE EXACT MOMENT of the request are accepted instead of rejected. The off-by-one is in middleware.py line 42 — the comparison uses `<` instead of `<=`."},
        {"role": "assistant", "content": "Confirmed: auth bug at middleware.py:42. Operator is `<` when it should be `<=`. Classic off-by-one on the jwt expiry boundary. Tests reproduce."},
    ]
    filler = []
    for i in range(38):
        pre.append({"role": "user", "content": f"investigate logging config item {i:02d}, verify formatter, check log levels"})
        pre.append({"role": "assistant", "content": f"logging config item {i:02d}: structured json, level INFO, formatter consistent"})
    for i in range(20):
        filler.append({"role": "user", "content": f"docs cleanup task {i:02d}: rewrite the section on configuration env vars"})
        filler.append({"role": "assistant", "content": f"docs section {i:02d} rewritten, table format applied"})
    return LongConvScenario(
        id="auth_bug_long",
        pre_turns=pre,
        anchor_turns=anchor,
        filler_turns=filler,
        question="Where exactly was the auth bug and what kind of bug was it?",
        expected_anchors=["middleware", "42", "jwt"],
    )


def all_scenarios() -> list[LongConvScenario]:
    return [make_scenario_db_choice(), make_scenario_auth_bug()]


def _check_anchors(text: str, anchors: list[str]) -> tuple[list[str], list[str]]:
    hits = [a for a in anchors if a.lower() in text.lower()]
    misses = [a for a in anchors if a not in hits]
    return hits, misses


def _extract_text(resp: dict) -> str:
    return "".join(
        b.get("text", "")
        for b in resp.get("content", [])
        if isinstance(b, dict)
    )


def _call(base_url: str, auth: str, payload: dict, extra_headers: dict | None = None) -> tuple[int, dict]:
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
        return r.status_code, r.json()


def run_vanilla_truncated(
    scenario: LongConvScenario,
    base_url: str,
    auth: str,
    model: str,
    keep_last_n: int = 8,
) -> LongConvResult:
    """Send conversation with only the last N turns + question. Anchor likely lost."""
    all_turns = scenario.pre_turns + scenario.anchor_turns + scenario.filler_turns
    kept = all_turns[-keep_last_n:] + [{"role": "user", "content": scenario.question}]
    t0 = time.time()
    try:
        _, resp = _call(base_url, auth, {"model": model, "max_tokens": 300, "messages": kept})
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check_anchors(text, scenario.expected_anchors)
        return LongConvResult(
            scenario_id=scenario.id, mode="vanilla_truncated", response=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            anchors_hit=hits, anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return LongConvResult(
            scenario_id=scenario.id, mode="vanilla_truncated", response="",
            input_tokens=0, output_tokens=0,
            anchors_hit=[], anchors_missed=scenario.expected_anchors,
            latency_ms=int((time.time() - t0) * 1000), error=str(e),
        )


def run_spillover(
    scenario: LongConvScenario,
    proxy_base_url: str,
    auth: str,
    model: str,
) -> LongConvResult:
    """Send full conversation through spillover; eviction archives mid-flight."""
    all_turns = scenario.pre_turns + scenario.anchor_turns + scenario.filler_turns
    full = all_turns + [{"role": "user", "content": scenario.question}]
    t0 = time.time()
    try:
        _, resp = _call(
            proxy_base_url, auth,
            {"model": model, "max_tokens": 300, "messages": full},
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check_anchors(text, scenario.expected_anchors)
        return LongConvResult(
            scenario_id=scenario.id, mode="spillover", response=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            anchors_hit=hits, anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return LongConvResult(
            scenario_id=scenario.id, mode="spillover", response="",
            input_tokens=0, output_tokens=0,
            anchors_hit=[], anchors_missed=scenario.expected_anchors,
            latency_ms=int((time.time() - t0) * 1000), error=str(e),
        )


def render_report(results: list[LongConvResult]) -> str:
    by_mode: dict[str, list[LongConvResult]] = {"vanilla_truncated": [], "spillover": []}
    for r in results:
        by_mode[r.mode].append(r)

    def _full_hit(rs):
        return sum(1 for r in rs if not r.anchors_missed) if rs else 0

    lines = ["# Long-conversation bench", "", "## summary", "", "| metric | vanilla_truncated | spillover |", "|---|---:|---:|"]
    v = by_mode["vanilla_truncated"]
    s = by_mode["spillover"]
    lines.append(f"| scenarios w/ all anchors hit | {_full_hit(v)}/{len(v)} | {_full_hit(s)}/{len(s)} |")
    lines.append(f"| total input_tokens | {sum(r.input_tokens for r in v)} | {sum(r.input_tokens for r in s)} |")
    lines.append(f"| total output_tokens | {sum(r.output_tokens for r in v)} | {sum(r.output_tokens for r in s)} |")
    lines.append(f"| total errors | {sum(1 for r in v if r.error)} | {sum(1 for r in s if r.error)} |")

    lines.append("\n## per-scenario\n")
    lines.append("| scenario | mode | hits | misses | input | output | latency_ms |")
    lines.append("|---|---|---|---|---:|---:|---:|")
    for r in results:
        hits = ",".join(r.anchors_hit) or "-"
        misses = ",".join(r.anchors_missed) or "-"
        lines.append(
            f"| {r.scenario_id} | {r.mode} | {hits} | {misses} | {r.input_tokens} | {r.output_tokens} | {r.latency_ms} |"
        )
    return "\n".join(lines) + "\n"
