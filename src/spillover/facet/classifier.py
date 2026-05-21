from __future__ import annotations

import re
from typing import Any, Literal

MemoryType = Literal["procedural", "episodic", "semantic", "priority"]

_PRIORITY_MARKERS = re.compile(
    r"(?i)\b(remember this|lembra disso|important|importante|never|nunca|always|sempre)\b"
)
_PROCEDURAL_MARKERS = re.compile(
    r"(?i)\b(step \d|first .* then|how to|run the|execute|invoke|call .*\(\))"
)
_SEMANTIC_MARKERS = re.compile(
    r"(?i)\b(is a|are a kind of|definition|convention|architecture|design choice)\b"
)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def classify(content: Any, tool_calls: list[dict] | None = None) -> MemoryType:
    text = _content_to_text(content)
    has_tools = bool(tool_calls)

    if _PRIORITY_MARKERS.search(text):
        return "priority"
    if has_tools or _PROCEDURAL_MARKERS.search(text):
        return "procedural"
    if _SEMANTIC_MARKERS.search(text):
        return "semantic"
    return "episodic"
