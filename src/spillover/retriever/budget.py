from __future__ import annotations

import sqlite3

from spillover.eviction.tokenizer import count_tokens
from spillover.retriever.vector import Hit


def trim_to_budget(
    db: sqlite3.Connection,
    hits: list[Hit],
    max_tokens: int,
) -> list[Hit]:
    if max_tokens <= 0 or not hits:
        return []
    ids = [h.episode_id for h in hits]
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"SELECT id, token_count, content_json FROM episodes WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    out: list[Hit] = []
    total = 0
    for hit in hits:
        row = by_id.get(hit.episode_id)
        if row is None:
            continue
        n = int(row["token_count"]) if row["token_count"] else count_tokens(row["content_json"])
        if total + n > max_tokens:
            break
        total += n
        out.append(hit)
    return out
