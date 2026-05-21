from spillover.counter_compact.detection import (
    detect_compaction,
    prune_old_seen_turns,
    record_seen_turns,
)
from spillover.storage.sqlite import open_project_db


def _msg(role, text):
    return {"role": role, "content": text}


def test_record_seen_turns_upserts(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        msgs = [
            _msg("user", "u1"),
            _msg("assistant", "a1"),
            _msg("user", "u2"),
            _msg("assistant", "a2"),
        ]
        record_seen_turns(db, "p1", msgs)
        rows = db.execute(
            "SELECT COUNT(*) FROM seen_turns WHERE project_id=?", ("p1",)
        ).fetchone()
        assert rows[0] == 2  # 2 assistant turns
        # Re-record: last_seen_ts updates, no duplicates
        record_seen_turns(db, "p1", msgs)
        rows = db.execute(
            "SELECT COUNT(*) FROM seen_turns WHERE project_id=?", ("p1",)
        ).fetchone()
        assert rows[0] == 2
    finally:
        db.close()


def test_detect_compaction_finds_missing(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        # Turn 1: see a1, a2, a3
        msgs_t1 = [
            _msg("assistant", "a1"),
            _msg("assistant", "a2"),
            _msg("assistant", "a3"),
        ]
        record_seen_turns(db, "p1", msgs_t1)
        # Turn 2: only a4 (CLI compacted a1+a2+a3 into something else)
        msgs_t2 = [_msg("assistant", "a4")]
        rescued = detect_compaction(db, "p1", msgs_t2)
        assert len(rescued) == 3
        contents = [r.content for r in rescued]
        assert "a1" in contents
        assert "a2" in contents
        assert "a3" in contents
    finally:
        db.close()


def test_detect_compaction_no_loss(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        msgs = [_msg("assistant", "a1"), _msg("assistant", "a2")]
        record_seen_turns(db, "p1", msgs)
        rescued = detect_compaction(db, "p1", msgs)
        assert rescued == []
    finally:
        db.close()


def test_prune_old_seen_turns(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        record_seen_turns(db, "p1", [_msg("assistant", "old")])
        # Manually backdate last_seen_ts
        db.execute("UPDATE seen_turns SET last_seen_ts=0 WHERE project_id=?", ("p1",))
        deleted = prune_old_seen_turns(db, "p1", ttl_hours=1)
        assert deleted == 1
    finally:
        db.close()
