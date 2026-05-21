from spillover.archive.writer import Turn, archive_raw
from spillover.retriever.lexical import _query_to_fts, bm25_topk
from spillover.storage.sqlite import open_project_db


def test_query_to_fts_strips_noise():
    out = _query_to_fts("where is the auth bug?")
    assert "where" in out
    assert "auth" in out
    assert "bug" in out
    assert "?" not in out
    # `is` and `the` are 2-char and 3-char -- the tokenizer keeps them (min length is 2),
    # which is fine because FTS5 strips its own stopwords with porter unicode61.


def test_query_to_fts_empty():
    assert _query_to_fts("???") == ""
    assert _query_to_fts("") == ""


def test_bm25_topk_finds_literal_match(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        for i, content in enumerate([
            "the auth bug is at middleware.py line 42",
            "SQLite chosen over Postgres",
            "Erica wife T1 diabetes Basaglar Fiasp",
        ]):
            archive_raw(
                db,
                Turn(
                    project_id="p1",
                    role="user",
                    content=content,
                    tool_calls=[],
                    code_refs=[],
                    token_count=5,
                    ts=i + 1,
                ),
            )
        hits = bm25_topk(db, "where was the auth bug", k=10)
        assert len(hits) >= 1
        assert hits[0].source == "bm25"
        # The auth bug episode is the only one containing 'auth' or 'bug'
        # Vector cosine would smear; BM25 finds it exactly.
        top_content = db.execute(
            "SELECT body FROM episodes_fts WHERE episode_id = ?",
            (hits[0].episode_id,),
        ).fetchone()
        assert "auth" in top_content["body"].lower() or "bug" in top_content["body"].lower()
    finally:
        db.close()


def test_bm25_topk_handles_no_fts_table(tmp_path):
    """If a project pre-dates Plan 8 FTS, bm25_topk returns [] not raise."""
    import sqlite3
    raw_path = tmp_path / "raw.db"
    conn = sqlite3.connect(raw_path)
    conn.row_factory = sqlite3.Row
    hits = bm25_topk(conn, "anything", k=10)
    assert hits == []
    conn.close()
