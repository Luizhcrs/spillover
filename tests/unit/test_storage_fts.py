from spillover.archive.writer import Turn, archive_raw
from spillover.storage.sqlite import open_project_db


def test_fts_index_populated_on_archive(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="auth bug at middleware.py line 42 jwt expiry",
                tool_calls=[],
                code_refs=[],
                token_count=10,
                ts=1,
            ),
        )
        rows = db.execute(
            "SELECT episode_id FROM episodes_fts WHERE body MATCH 'middleware'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["episode_id"] == eid
    finally:
        db.close()


def test_fts_matches_phrase(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="SQLite chosen for local-only deployment",
                tool_calls=[],
                code_refs=[],
                token_count=5,
                ts=1,
            ),
        )
        rows = db.execute(
            "SELECT episode_id FROM episodes_fts WHERE body MATCH 'SQLite OR local'"
        ).fetchall()
        assert any(r["episode_id"] == eid for r in rows)
    finally:
        db.close()
