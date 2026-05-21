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
            "SELECT episode_id FROM episodes_fts WHERE body MATCH 'middleware*'"
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
                content="SQLite chosen for local deployment",
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


def test_fts_preserves_compound_tokens(tmp_path):
    """New tokenchars ./-_: keeps compound identifiers intact."""
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="watermark 0.85 with char/4 tokenizer and letsencryptresolver",
                tool_calls=[],
                code_refs=[],
                token_count=10,
                ts=1,
            ),
        )
        for q in ['"0.85"', '"char/4"', '"letsencryptresolver"']:
            rows = db.execute(
                "SELECT episode_id FROM episodes_fts WHERE body MATCH ?", (q,)
            ).fetchall()
            assert any(r["episode_id"] == eid for r in rows), (
                f"compound token {q} not found"
            )
    finally:
        db.close()
