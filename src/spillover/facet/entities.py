from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Entity:
    name: str
    kind: str  # "file" | "url" | "identifier" | "command"


_FILE_PATH = re.compile(
    r"(?<![A-Za-z0-9])"
    r"((?:[A-Za-z]:[\\/]|/)?(?:[\w.\-]+[\\/])*[\w.\-]+\.\w+)"
)
_URL = re.compile(r"https?://[^\s)>\"]+")
_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Za-z0-9]+|[a-z_][a-zA-Z0-9_]+)(?=\()")
_COMMAND = re.compile(r"`([a-z][a-z0-9_\- ]{1,40})`")


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content", "")))
        return "\n".join(parts)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
    return ""


def extract_entities(content: Any) -> list[Entity]:
    text = _content_to_text(content)
    seen: set[tuple[str, str]] = set()
    out: list[Entity] = []
    for m in _FILE_PATH.finditer(text):
        key = (m.group(1), "file")
        if key not in seen:
            seen.add(key)
            out.append(Entity(name=m.group(1), kind="file"))
    for m in _URL.finditer(text):
        key = (m.group(0), "url")
        if key not in seen:
            seen.add(key)
            out.append(Entity(name=m.group(0), kind="url"))
    for m in _IDENTIFIER.finditer(text):
        key = (m.group(1), "identifier")
        if key not in seen:
            seen.add(key)
            out.append(Entity(name=m.group(1), kind="identifier"))
    for m in _COMMAND.finditer(text):
        key = (m.group(1).strip(), "command")
        if key not in seen:
            seen.add(key)
            out.append(Entity(name=m.group(1).strip(), kind="command"))
    return out
