from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import kuzu

_SCHEMA_PATH = Path(__file__).with_name("kuzu_schema.cypher")
_CACHE: OrderedDict[str, kuzu.Connection] = OrderedDict()
_INITIALIZED: set[str] = set()
_LOCK = threading.Lock()
_MAX_CACHE = 32


def project_kuzu_dir(db_root: Path, project_id: str) -> Path:
    return db_root / "projects" / project_id / "kuzu"


def _init_schema(conn: kuzu.Connection, cache_key: str) -> None:
    if cache_key in _INITIALIZED:
        return
    schema = _SCHEMA_PATH.read_text(encoding="utf-8")
    for statement in [s.strip() for s in schema.split(";") if s.strip()]:
        conn.execute(statement)
    _INITIALIZED.add(cache_key)


def open_project_kuzu(db_root: Path, project_id: str) -> kuzu.Connection:
    key = f"{db_root}:{project_id}"
    with _LOCK:
        existing = _CACHE.get(key)
        if existing is not None:
            _CACHE.move_to_end(key)
            return existing
        path = project_kuzu_dir(db_root, project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = kuzu.Database(str(path))
        conn = kuzu.Connection(db)
        _init_schema(conn, key)
        _CACHE[key] = conn
        while len(_CACHE) > _MAX_CACHE:
            _CACHE.popitem(last=False)
        return conn


def clear_kuzu_cache() -> None:
    """Test helper -- drop all cached connections."""
    with _LOCK:
        _CACHE.clear()
        _INITIALIZED.clear()
