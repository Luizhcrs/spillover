from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any


def _normalize(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        return json.dumps(content, sort_keys=True, ensure_ascii=False)
    return str(content)


@lru_cache(maxsize=4096)
def _count_for_hash(content_hash: str, text: str) -> int:
    # Heuristic: 1 token per ~4 characters for English/code mix.
    # The Anthropic SDK does not expose a synchronous offline tokenizer in this
    # version; we use a stable approximation here and refine in Plan 2 when the
    # facet pipeline calls the real countTokens endpoint asynchronously.
    if not text:
        return 0
    return max(1, len(text) // 4)


def count_tokens(content: Any) -> int:
    text = _normalize(content)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return _count_for_hash(h, text)
