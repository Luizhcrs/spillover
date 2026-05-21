from __future__ import annotations

import json
from functools import lru_cache
from typing import Any


def _normalize(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, (dict, list, tuple)):
        return json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    return str(content)


@lru_cache(maxsize=4096)
def _count_for_text(text: str) -> int:
    """Char-based heuristic: ~1 token per 4 characters.

    The Anthropic SDK does not expose a synchronous offline tokenizer in this
    version; we use a stable approximation here and refine in Plan 2 when the
    facet pipeline calls the real countTokens endpoint asynchronously.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def count_tokens(content: Any) -> int:
    """Count tokens for arbitrary content (str | bytes | dict | list | tuple | None).

    Returns 0 for empty/None content; >=1 for any non-empty input. Memoized over
    a normalized string representation of the input.
    """
    return _count_for_text(_normalize(content))
