from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_VEC_SCHEMA_PATH = Path(__file__).with_name("vec_schema.sql")
_FTS_SCHEMA_PATH = Path(__file__).with_name("fts_schema.sql")


def project_db_path(db_root: Path, project_id: str) -> Path:
    return db_root / "projects" / project_id / "episodes.db"


def open_project_db(db_root: Path, project_id: str) -> sqlite3.Connection:
    path = project_db_path(db_root, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(_VEC_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(_FTS_SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn
