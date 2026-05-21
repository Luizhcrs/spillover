from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from spillover.logging import get_logger

log = get_logger("counter_compact")


@dataclass(frozen=True)
class SeenTurn:
    turn_hash: str
    turn_index: int
    content_json: str
    first_seen_ts: int
    last_seen_ts: int


@dataclass(frozen=True)
class RescuedTurn:
    role: str
    content: Any
    token_count: int
    original_hash: str


def _hash_assistant_message(msg: dict) -> str:
    payload = json.dumps(
        {"role": msg.get("role"), "content": msg.get("content")},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_seen_turns(
    db: sqlite3.Connection,
    project_id: str,
    messages: list[dict],
) -> None:
    """Upsert every assistant message in the conversation into seen_turns.

    Keeps last_seen_ts current so we can prune stale rows later.
    """
    now = int(time.time() * 1000)
    for idx, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        h = _hash_assistant_message(msg)
        content_json = json.dumps(msg.get("content"), ensure_ascii=False)
        existing = db.execute(
            "SELECT first_seen_ts FROM seen_turns "
            "WHERE project_id=? AND turn_hash=?",
            (project_id, h),
        ).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO seen_turns(project_id, turn_hash, turn_index, "
                "content_json, first_seen_ts, last_seen_ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, h, idx, content_json, now, now),
            )
        else:
            db.execute(
                "UPDATE seen_turns SET last_seen_ts=? "
                "WHERE project_id=? AND turn_hash=?",
                (now, project_id, h),
            )


def detect_compaction(
    db: sqlite3.Connection,
    project_id: str,
    messages: list[dict],
) -> list[RescuedTurn]:
    """Compare the current inbound messages against seen_turns.

    Returns the list of assistant turns the proxy previously witnessed that
    have now disappeared from the conversation, ordered by their original
    turn_index.
    """
    current_hashes: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        current_hashes.add(_hash_assistant_message(msg))

    rows = db.execute(
        "SELECT turn_hash, turn_index, content_json FROM seen_turns "
        "WHERE project_id=? ORDER BY turn_index ASC",
        (project_id,),
    ).fetchall()

    rescued: list[RescuedTurn] = []
    for row in rows:
        if row["turn_hash"] in current_hashes:
            continue
        # This previously-seen turn is missing -> compaction suspected.
        content = json.loads(row["content_json"])
        # crude token count: char/4 like the heuristic tokenizer
        from spillover.eviction.tokenizer import count_tokens

        rescued.append(
            RescuedTurn(
                role="assistant",
                content=content,
                token_count=count_tokens(content),
                original_hash=row["turn_hash"],
            )
        )

    if rescued:
        log.warning(
            "compaction_detected project=%s rescued_count=%d",
            project_id,
            len(rescued),
        )

    return rescued


def prune_old_seen_turns(
    db: sqlite3.Connection,
    project_id: str,
    ttl_hours: int = 72,
) -> int:
    """Delete seen_turns rows not refreshed within ttl_hours. Returns count."""
    cutoff = int(time.time() * 1000) - ttl_hours * 3600 * 1000
    cur = db.execute(
        "DELETE FROM seen_turns WHERE project_id=? AND last_seen_ts < ?",
        (project_id, cutoff),
    )
    return cur.rowcount
