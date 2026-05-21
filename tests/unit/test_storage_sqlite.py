import sqlite3

import pytest

from spillover.storage.sqlite import open_project_db, project_db_path


def test_project_db_path_uses_root(tmp_path):
    p = project_db_path(tmp_path, "abc123")
    assert p == tmp_path / "projects" / "abc123" / "episodes.db"


def test_open_project_db_creates_dir_and_tables(tmp_path):
    db = open_project_db(tmp_path, "abc123")
    try:
        cur = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert "episodes" in tables
        assert "seen_turns" in tables
        # WAL mode active
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        db.close()


def test_open_project_db_idempotent(tmp_path):
    open_project_db(tmp_path, "abc123").close()
    db = open_project_db(tmp_path, "abc123")
    try:
        # Re-opening does not error or wipe tables
        cur = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert "episodes" in tables
    finally:
        db.close()


def test_episodes_id_rejects_null(tmp_path):
    db = open_project_db(tmp_path, "abc123")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO episodes (id, project_id, role, content_json, token_count, ts, hash) "
                "VALUES (NULL, 'p', 'user', '{}', 0, 0, 'h')"
            )
    finally:
        db.close()


def test_episodes_evicted_rejects_invalid(tmp_path):
    db = open_project_db(tmp_path, "abc123")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO episodes"
                " (id, project_id, role, content_json, token_count, ts, hash, evicted)"
                " VALUES ('e1', 'p', 'user', '{}', 0, 0, 'h', 2)"
            )
    finally:
        db.close()
