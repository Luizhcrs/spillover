import struct

from spillover.storage.sqlite import open_project_db


def _b(floats):
    return struct.pack(f"<{len(floats)}f", *floats)


def test_vec_episodes_table_exists(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        tables = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        }
        assert "vec_episodes" in tables
    finally:
        db.close()


def test_vec_episodes_accepts_insert_and_query(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        emb = [0.1] * 768
        db.execute(
            "INSERT INTO vec_episodes(episode_id, embedding, memory_type, importance, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            ("e1", _b(emb), "episodic", 1.0, 0),
        )
        rows = db.execute(
            "SELECT episode_id FROM vec_episodes WHERE episode_id = ?", ("e1",)
        ).fetchall()
        assert len(rows) == 1
    finally:
        db.close()
