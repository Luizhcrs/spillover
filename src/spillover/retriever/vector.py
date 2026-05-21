from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass


@dataclass
class Hit:
    episode_id: str
    score: float
    memory_type: str | None = None
    importance: float | None = None
    ts: int | None = None
    source: str = "vector"


def _floats_to_bytes(v: list[float]) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def vector_topk(
    db: sqlite3.Connection, embedding: list[float], k: int = 50
) -> list[Hit]:
    rows = db.execute(
        "SELECT episode_id, distance, memory_type, importance, ts "
        "FROM vec_episodes "
        "WHERE embedding MATCH ? "
        "ORDER BY distance LIMIT ?",
        (_floats_to_bytes(embedding), k),
    ).fetchall()
    return [
        Hit(
            episode_id=r["episode_id"],
            score=1.0 - float(r["distance"]),
            memory_type=r["memory_type"],
            importance=r["importance"],
            ts=r["ts"],
            source="vector",
        )
        for r in rows
    ]
