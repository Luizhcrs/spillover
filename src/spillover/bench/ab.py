from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunResult:
    task_id: str
    used_spillover: bool
    response: str
    input_tokens: int
    output_tokens: int
    regression_markers: list[str] = field(default_factory=list)


_REGRESSION_PATTERNS = [
    "i don't remember",
    "i do not remember",
    "could you remind",
    "context was lost",
    "i don't have access to the earlier",
    "no longer have the earlier",
    "no recollection",
    "i've forgotten",
    "preciso que voce me lembre",
    "nao me lembro",
]


def _detect_regressions(response: str) -> list[str]:
    text = response.lower()
    return [p for p in _REGRESSION_PATTERNS if p in text]


def summarize_runs(runs: list[RunResult]) -> dict:
    by_mode: dict[bool, list[RunResult]] = {True: [], False: []}
    for r in runs:
        by_mode[r.used_spillover].append(r)
    summary = {}
    for used_spillover, results in by_mode.items():
        if not results:
            continue
        total_in = sum(r.input_tokens for r in results)
        total_out = sum(r.output_tokens for r in results)
        regs = sum(len(r.regression_markers) for r in results)
        summary["spillover" if used_spillover else "vanilla"] = {
            "tasks": len(results),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "regression_markers": regs,
        }
    return summary


def render_markdown(summary: dict) -> str:
    lines = ["# spillover A/B benchmark", "", "| metric | vanilla | spillover |", "|---|---:|---:|"]
    v = summary.get("vanilla", {})
    s = summary.get("spillover", {})
    for key in ("tasks", "total_input_tokens", "total_output_tokens", "regression_markers"):
        lines.append(f"| {key} | {v.get(key, '-')} | {s.get(key, '-')} |")
    return "\n".join(lines) + "\n"


def detect_regressions_for_response(response: str) -> list[str]:
    return _detect_regressions(response)
