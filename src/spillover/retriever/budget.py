from __future__ import annotations

import sqlite3

from spillover.eviction.tokenizer import count_tokens
from spillover.retriever.vector import Hit


def trim_to_budget(
    db: sqlite3.Connection,
    hits: list[Hit],
    max_tokens: int,
) -> list[Hit]:
    """Trim hits so total token count fits under max_tokens.

    Uses the cached `episodes.token_count` when present; falls back to
    re-counting from content_json.
    """
    if max_tokens <= 0 or not hits:
        return []
    out: list[Hit] = []
    total = 0
    for hit in hits:
        row = db.execute(
            "SELECT token_count, content_json FROM episodes WHERE id = ?",
            (hit.episode_id,),
        ).fetchone()
        if row is None:
            continue
        n = int(row["token_count"]) if row["token_count"] else count_tokens(row["content_json"])
        if total + n > max_tokens:
            break
        total += n
        out.append(hit)
    return out
