import struct

from spillover.archive.writer import Turn, archive_raw
from spillover.decay.scheduler import _apply_decay_for_project
from spillover.storage.sqlite import open_project_db


def test_decay_lowers_importance_with_age(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="x",
                tool_calls=[],
                code_refs=[],
                token_count=1,
                ts=0,  # ancient
            ),
        )
        # Pre-populate vec row
        db.execute(
            "INSERT INTO vec_episodes(episode_id, embedding, memory_type, "
            "importance, ts) VALUES (?, ?, ?, ?, ?)",
            (eid, struct.pack("<768f", *[0.0] * 768), "episodic", 1.0, 0),
        )
    finally:
        db.close()

    n = _apply_decay_for_project(tmp_path, "p1")
    assert n == 1

    db = open_project_db(tmp_path, "p1")
    try:
        new_imp = db.execute(
            "SELECT importance FROM vec_episodes WHERE episode_id=?", (eid,)
        ).fetchone()[0]
        # ts=0 -> very old -> decay drives importance toward 0
        assert new_imp < 0.5
    finally:
        db.close()


def test_decay_skips_pinned(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="x pinned",
                tool_calls=[],
                code_refs=[],
                token_count=1,
                ts=0,
            ),
        )
        db.execute("UPDATE episodes SET pinned=1 WHERE id=?", (eid,))
        db.execute(
            "INSERT INTO vec_episodes(episode_id, embedding, memory_type, "
            "importance, ts) VALUES (?, ?, ?, ?, ?)",
            (eid, struct.pack("<768f", *[0.0] * 768), "episodic", 1.0, 0),
        )
    finally:
        db.close()

    _apply_decay_for_project(tmp_path, "p1")

    db = open_project_db(tmp_path, "p1")
    try:
        imp = db.execute(
            "SELECT importance FROM vec_episodes WHERE episode_id=?", (eid,)
        ).fetchone()[0]
        assert imp == 1.0  # untouched
    finally:
        db.close()
