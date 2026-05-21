from __future__ import annotations

from pathlib import Path

import kuzu

_SCHEMA_PATH = Path(__file__).with_name("kuzu_schema.cypher")


def project_kuzu_dir(db_root: Path, project_id: str) -> Path:
    return db_root / "projects" / project_id / "kuzu"


def open_project_kuzu(db_root: Path, project_id: str) -> kuzu.Connection:
    path = project_kuzu_dir(db_root, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = kuzu.Database(str(path))
    conn = kuzu.Connection(db)
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    for statement in [s.strip() for s in schema.split(";") if s.strip()]:
        conn.execute(statement)
    return conn
