from __future__ import annotations

import re
from typing import Any


_OPEN_TASK_PATTERNS = [
    re.compile(r"(?i)\bTODO\b"),
    re.compile(r"(?i)\bFIXME\b"),
    re.compile(r"(?i)\bXXX\b"),
    re.compile(r"(?i)\bHACK\b"),
    re.compile(r"(?i)\bpending\b"),
    re.compile(r"(?i)\bnot yet (done|implemented|finished)\b"),
    re.compile(r"(?i)\bstill (need to|have to|must)\b"),
    re.compile(r"(?i)\bnext step[s]? (is|are)\b"),
    re.compile(r"(?i)\bfaltam? (fazer|implementar)\b"),  # PT-BR
    re.compile(r"(?i)\bainda (preciso|falta|tem que)\b"),
    re.compile(r"(?i)\bproxim[oa] passo\b"),
]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def has_open_task(content: Any) -> bool:
    text = _content_to_text(content)
    return any(p.search(text) for p in _OPEN_TASK_PATTERNS)
