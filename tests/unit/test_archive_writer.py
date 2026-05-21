import json
import time

from spillover.archive.writer import Turn, archive_raw
from spillover.storage.sqlite import open_project_db


def test_archive_raw_inserts_and_returns_id(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        turn = Turn(
            project_id="p1",
            role="assistant",
            content=[{"type": "text", "text": "hello"}],
            tool_calls=[],
            code_refs=[],
            token_count=5,
            ts=int(time.time() * 1000),
        )
        eid = archive_raw(db, turn)
        assert isinstance(eid, str)
        row = db.execute("SELECT * FROM episodes WHERE id = ?", (eid,)).fetchone()
        assert row is not None
        assert row["role"] == "assistant"
        assert json.loads(row["content_json"])[0]["text"] == "hello"
        assert row["evicted"] == 0
        assert row["facet_pending"] == 1
        assert row["token_count"] == 5
    finally:
        db.close()


def test_archive_raw_dedup_by_hash(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        turn = Turn(
            project_id="p1",
            role="user",
            content="same text",
            tool_calls=[],
            code_refs=[],
            token_count=2,
            ts=1700000000000,
        )
        eid1 = archive_raw(db, turn)
        eid2 = archive_raw(db, turn)
        assert eid1 == eid2
        count = db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        assert count == 1
    finally:
        db.close()
