from __future__ import annotations

import re
import sqlite3

from spillover.retriever.vector import Hit

# Lightweight tokenizer for query sanitization. FTS5 MATCH syntax is sensitive
# to special chars and operators; we extract bag-of-words and OR-join.
_TOKEN = re.compile(r"[A-Za-z0-9_]{2,}")


def _query_to_fts(query: str) -> str:
    tokens = _TOKEN.findall(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def bm25_topk(
    db: sqlite3.Connection, query: str, k: int = 50
) -> list[Hit]:
    fts_q = _query_to_fts(query)
    if not fts_q:
        return []
    try:
        rows = db.execute(
            "SELECT f.episode_id, bm25(episodes_fts) AS score, "
            "       e.memory_type, e.ts "
            "FROM episodes_fts f "
            "JOIN episodes e ON e.id = f.episode_id "
            "WHERE f.body MATCH ? "
            "ORDER BY score ASC LIMIT ?",
            (fts_q, k),
        ).fetchall()
    except sqlite3.OperationalError:
        # FTS table absent (older DB) -- return empty rather than crash
        return []
    # bm25() returns negative scores; lower is better. We invert so higher = more relevant.
    return [
        Hit(
            episode_id=r["episode_id"],
            score=-float(r["score"]),
            memory_type=r["memory_type"],
            importance=None,
            ts=r["ts"],
            source="bm25",
        )
        for r in rows
    ]
