import pytest

from spillover.archive.writer import Turn, archive_raw
from spillover.facet.worker import FacetEvent, _process_one
from spillover.storage.sqlite import open_project_db


def test_archive_durability_after_handle_drop(tmp_path):
    """Simulates SIGKILL mid-archive: some episodes already written, some not.
    On 'restart' (reopening db), the persisted set must be intact."""
    db = open_project_db(tmp_path, "p1")
    try:
        eid_a = archive_raw(
            db,
            Turn(project_id="p1", role="user", content="A",
                 tool_calls=[], code_refs=[], token_count=1, ts=1),
        )
        eid_b = archive_raw(
            db,
            Turn(project_id="p1", role="user", content="B",
                 tool_calls=[], code_refs=[], token_count=1, ts=2),
        )
    finally:
        db.close()  # simulate crash before episodes C, D, E

    # "Restart"
    db = open_project_db(tmp_path, "p1")
    try:
        rows = db.execute(
            "SELECT id, content_json, facet_pending FROM episodes ORDER BY ts"
        ).fetchall()
        assert len(rows) == 2
        assert {r["id"] for r in rows} == {eid_a, eid_b}
        # All survivors should still be facet_pending=1 (worker didn't run pre-crash)
        assert all(r["facet_pending"] == 1 for r in rows)
    finally:
        db.close()


@pytest.mark.slow
def test_facet_pipeline_can_replay_survivors(tmp_path):
    """After 'crash', the facet worker can pick up pending episodes and process them."""
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(project_id="p1", role="user",
                 content="auth bug at middleware.py:42",
                 tool_calls=[], code_refs=[], token_count=10, ts=1),
        )
    finally:
        db.close()
    _process_one(FacetEvent(project_id="p1", episode_id=eid, db_root=tmp_path))
    db = open_project_db(tmp_path, "p1")
    try:
        row = db.execute(
            "SELECT facet_pending, memory_type FROM episodes WHERE id=?", (eid,)
        ).fetchone()
        assert row["facet_pending"] == 0
        assert row["memory_type"] is not None
        vec_row = db.execute(
            "SELECT episode_id FROM vec_episodes WHERE episode_id=?", (eid,)
        ).fetchone()
        assert vec_row is not None
    finally:
        db.close()


def test_seen_turns_survives_restart(tmp_path):
    """seen_turns table durability: counter-compaction defense survives crash."""
    from spillover.counter_compact.detection import record_seen_turns
    db = open_project_db(tmp_path, "p1")
    try:
        record_seen_turns(db, "p1", [
            {"role": "assistant", "content": "this must survive crash"},
        ])
    finally:
        db.close()
    db = open_project_db(tmp_path, "p1")
    try:
        rows = db.execute(
            "SELECT content_json FROM seen_turns WHERE project_id=?", ("p1",)
        ).fetchall()
        assert len(rows) == 1
        assert "must survive" in rows[0]["content_json"]
    finally:
        db.close()
