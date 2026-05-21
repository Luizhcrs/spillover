from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Turn:
    project_id: str
    role: str
    content: Any
    tool_calls: list[dict] = field(default_factory=list)
    code_refs: list[dict] = field(default_factory=list)
    token_count: int = 0
    ts: int = 0
    compaction_rescued: bool = False


def _hash_turn(turn: Turn) -> str:
    payload = json.dumps(
        {
            "role": turn.role,
            "content": turn.content,
            "tool_calls": turn.tool_calls,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def archive_raw(db: sqlite3.Connection, turn: Turn) -> str:
    h = _hash_turn(turn)
    existing = db.execute("SELECT id FROM episodes WHERE hash = ?", (h,)).fetchone()
    if existing is not None:
        return existing["id"]
    eid = str(uuid.uuid4())
    try:
        db.execute(
            """
            INSERT INTO episodes (
                id, project_id, role, content_json, tool_calls_json,
                code_refs_json, token_count, ts, hash, compaction_rescued
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eid,
                turn.project_id,
                turn.role,
                json.dumps(turn.content, ensure_ascii=False),
                json.dumps(turn.tool_calls, ensure_ascii=False),
                json.dumps(turn.code_refs, ensure_ascii=False),
                turn.token_count,
                turn.ts,
                h,
                1 if turn.compaction_rescued else 0,
            ),
        )
        body_text = (
            turn.content
            if isinstance(turn.content, str)
            else json.dumps(turn.content, ensure_ascii=False)
        )
        try:
            db.execute(
                "INSERT INTO episodes_fts(episode_id, body) VALUES (?, ?)",
                (eid, body_text),
            )
        except sqlite3.IntegrityError:
            pass  # already indexed
        return eid
    except sqlite3.IntegrityError:
        existing = db.execute(
            "SELECT id FROM episodes WHERE hash = ?", (h,)
        ).fetchone()
        if existing is None:
            raise
        return existing["id"]
