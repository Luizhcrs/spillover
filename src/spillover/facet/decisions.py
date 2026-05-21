from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Decision:
    hash: str
    summary: str


@dataclass(frozen=True)
class CodeRef:
    path: str
    line: int | None = None
    op: str | None = None  # "read" | "write" | "edit" | "run"


_DECISION_PT = re.compile(
    r"(?im)^(?:.*?\b(decidi|escolhi|abandonei|optei|preferi)\b.{1,200})$"
)
_DECISION_EN = re.compile(
    r"(?im)^(?:.*?\b(decided|chose|abandoned|opted|picked|going with)\b.{1,200})$"
)
_BECAUSE = re.compile(
    r"(?im)\b(porque|pq|because|reason|motivo)\b[:\s].{1,200}"
)


def _summary(line: str) -> str:
    s = line.strip()
    if len(s) > 200:
        s = s[:200] + "..."
    return s


def _hash_summary(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        return "\n".join(parts)
    return ""


def extract_decisions(content: Any) -> list[Decision]:
    text = _content_to_text(content)
    seen: set[str] = set()
    out: list[Decision] = []
    for regex in (_DECISION_PT, _DECISION_EN, _BECAUSE):
        for m in regex.finditer(text):
            summary = _summary(m.group(0))
            h = _hash_summary(summary)
            if h in seen:
                continue
            seen.add(h)
            out.append(Decision(hash=h, summary=summary))
    return out


_TOOL_TO_OP = {
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "Bash": "run",
    "PowerShell": "run",
}


def extract_code_refs(tool_calls: list[dict]) -> list[CodeRef]:
    out: list[CodeRef] = []
    seen: set[tuple[str, int | None, str | None]] = set()
    for call in tool_calls or []:
        name = call.get("name")
        inp = call.get("input") or {}
        op = _TOOL_TO_OP.get(name)
        path = inp.get("file_path") or inp.get("path")
        if path is None:
            continue
        key = (path, None, op)
        if key in seen:
            continue
        seen.add(key)
        out.append(CodeRef(path=path, line=None, op=op))
    return out
