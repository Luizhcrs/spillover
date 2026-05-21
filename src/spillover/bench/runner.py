from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass
class TaskResult:
    task_id: str
    mode: str  # "vanilla" | "spillover"
    response_text: str
    input_tokens: int
    output_tokens: int
    anchors_hit: list[str] = field(default_factory=list)
    anchors_missed: list[str] = field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None


def _call_anthropic(
    base_url: str,
    auth: str,
    payload: dict,
    extra_headers: dict | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict]:
    headers = {
        "Authorization": auth,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{base_url}/v1/messages", json=payload, headers=headers)
        r.raise_for_status()
        return r.status_code, r.json()


def _extract_text(resp: dict) -> str:
    return "".join(
        b.get("text", "")
        for b in resp.get("content", [])
        if isinstance(b, dict)
    )


def _check_anchors(text: str, anchors: list[str]) -> tuple[list[str], list[str]]:
    hits = [a for a in anchors if a.lower() in text.lower()]
    misses = [a for a in anchors if a not in hits]
    return hits, misses


def run_task_vanilla(
    task: dict,
    base_url: str,
    auth: str,
    model: str = "claude-haiku-4-5-20251001",
) -> TaskResult:
    """Vanilla = send history + question as one conversation, no LTM injection."""
    messages = list(task["history"]) + [{"role": "user", "content": task["question"]}]
    payload = {
        "model": model,
        "max_tokens": 200,
        "messages": messages,
    }
    t0 = time.time()
    try:
        _, resp = _call_anthropic(base_url, auth, payload)
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check_anchors(text, task["expected_anchors"])
        return TaskResult(
            task_id=task["id"],
            mode="vanilla",
            response_text=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            anchors_hit=hits,
            anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return TaskResult(
            task_id=task["id"],
            mode="vanilla",
            response_text="",
            input_tokens=0,
            output_tokens=0,
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def run_task_spillover(
    task: dict,
    proxy_base_url: str,  # http://127.0.0.1:8787/p/<sha1>
    auth: str,
    model: str = "claude-haiku-4-5-20251001",
    seed_first: bool = True,
) -> TaskResult:
    """spillover mode: seed history in a first call (force eviction), then ask question alone.

    Tests whether LTM retrieval restores the answer despite the question being sent
    without the history.
    """
    if seed_first:
        seed_messages = list(task["history"]) + [
            {"role": "user", "content": "(internal: seed turn for retriever)"}
        ]
        try:
            _call_anthropic(
                proxy_base_url,
                auth,
                {
                    "model": model,
                    "max_tokens": 30,
                    "messages": seed_messages,
                },
                extra_headers={"anthropic-beta": "oauth-2025-04-20"},
            )
        except Exception:
            pass
        # Give facet pipeline a beat
        time.sleep(1.5)

    payload = {
        "model": model,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": task["question"]}],
    }
    t0 = time.time()
    try:
        _, resp = _call_anthropic(
            proxy_base_url,
            auth,
            payload,
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
        text = _extract_text(resp)
        usage = resp.get("usage", {})
        hits, misses = _check_anchors(text, task["expected_anchors"])
        return TaskResult(
            task_id=task["id"],
            mode="spillover",
            response_text=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            anchors_hit=hits,
            anchors_missed=misses,
            latency_ms=int((time.time() - t0) * 1000),
        )
    except Exception as e:
        return TaskResult(
            task_id=task["id"],
            mode="spillover",
            response_text="",
            input_tokens=0,
            output_tokens=0,
            latency_ms=int((time.time() - t0) * 1000),
            error=str(e),
        )


def run_ab(
    tasks_path: Path,
    auth: str,
    proxy_base_url: str,
    vanilla_base_url: str = "https://api.anthropic.com",
    model: str = "claude-haiku-4-5-20251001",
) -> list[TaskResult]:
    results: list[TaskResult] = []
    with tasks_path.open(encoding="utf-8") as f:
        tasks = [json.loads(line) for line in f if line.strip()]
    for task in tasks:
        results.append(run_task_vanilla(task, vanilla_base_url, auth, model=model))
        results.append(run_task_spillover(task, proxy_base_url, auth, model=model))
    return results


def render_ab_report(results: list[TaskResult]) -> str:
    by_mode: dict[str, list[TaskResult]] = {"vanilla": [], "spillover": []}
    for r in results:
        by_mode[r.mode].append(r)
    lines = [
        "# spillover A/B benchmark",
        "",
        "## summary",
        "",
        "| metric | vanilla | spillover |",
        "|---|---:|---:|",
    ]
    for mode in ("vanilla", "spillover"):
        if not by_mode[mode]:
            continue
    v = by_mode["vanilla"]
    s = by_mode["spillover"]

    def _ratio(rs: list[TaskResult]) -> str:
        if not rs:
            return "-"
        full = sum(1 for r in rs if r.anchors_missed == [])
        return f"{full}/{len(rs)}"

    def _toks(rs: list[TaskResult], field_: str) -> int:
        return sum(getattr(r, field_) for r in rs)

    lines.append(f"| tasks with all anchors hit | {_ratio(v)} | {_ratio(s)} |")
    lines.append(
        f"| total input_tokens | {_toks(v, 'input_tokens')} | {_toks(s, 'input_tokens')} |"
    )
    lines.append(
        f"| total output_tokens | {_toks(v, 'output_tokens')} | {_toks(s, 'output_tokens')} |"
    )
    lines.append(
        f"| total errors | {sum(1 for r in v if r.error)} | {sum(1 for r in s if r.error)} |"
    )

    lines.append("\n## per-task\n")
    lines.append("| task | mode | hits | misses | input | output | latency_ms |")
    lines.append("|---|---|---|---|---:|---:|---:|")
    for r in results:
        hits = ",".join(r.anchors_hit) or "-"
        misses = ",".join(r.anchors_missed) or "-"
        lines.append(
            f"| {r.task_id} | {r.mode} | {hits} | {misses} "
            f"| {r.input_tokens} | {r.output_tokens} | {r.latency_ms} |"
        )
    return "\n".join(lines) + "\n"


def main_offline_demo(tasks_path: Path, results_path: Path) -> None:
    """Read pre-scored results, render markdown -- same as Plan 4 bench/ab.py."""
    raw = json.loads(tasks_path.read_text(encoding="utf-8"))
    results = [TaskResult(**r) for r in raw]
    results_path.write_text(render_ab_report(results), encoding="utf-8")
