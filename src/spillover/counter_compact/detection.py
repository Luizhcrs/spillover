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
    window_seconds: int = 600,
    max_rescue: int = 20,
) -> list[RescuedTurn]:
    """Compare current inbound messages against RECENTLY-seen turns.

    Only rescue turns the proxy witnessed in the last `window_seconds` AND
    that are now missing from the current message list. This avoids
    rescuing the entire historical seen_turns backlog (which accumulates
    across sessions for a per-cwd project_id) every time a new session
    starts fresh -- the previous behavior surfaced ~2700 false-positive
    "rescues" per request on long-lived projects.

    Hard cap `max_rescue` so that a single false-positive cluster can't
    inflate the payload by orders of magnitude.
    """
    current_hashes: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        current_hashes.add(_hash_assistant_message(msg))

    cutoff_ms = int(time.time() * 1000) - window_seconds * 1000
    rows = db.execute(
        "SELECT turn_hash, turn_index, content_json FROM seen_turns "
        "WHERE project_id=? AND last_seen_ts >= ? "
        "ORDER BY turn_index ASC LIMIT ?",
        (project_id, cutoff_ms, max_rescue),
    ).fetchall()

    rescued: list[RescuedTurn] = []
    for row in rows:
        if row["turn_hash"] in current_hashes:
            continue
        # Previously-seen turn (within window) is missing -> compaction suspected.
        content = json.loads(row["content_json"])
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
            "compaction_detected project=%s rescued_count=%d window_seconds=%d",
            project_id,
            len(rescued),
            window_seconds,
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
