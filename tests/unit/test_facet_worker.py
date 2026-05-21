import asyncio
import time

import pytest

from spillover.archive.writer import Turn, archive_raw
from spillover.facet.worker import FacetEvent, FacetWorker, _process_one
from spillover.storage.sqlite import open_project_db


@pytest.mark.slow
def test_process_one_writes_vec_and_updates_pending(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content=(
                    "Refactor src/foo.py to use config from env. "
                    "decidi usar SQLite porque e local."
                ),
                tool_calls=[{"name": "Read", "input": {"file_path": "src/foo.py"}}],
                code_refs=[],
                token_count=20,
                ts=int(time.time() * 1000),
            ),
        )
    finally:
        db.close()

    _process_one(FacetEvent(project_id="p1", episode_id=eid, db_root=tmp_path))

    db = open_project_db(tmp_path, "p1")
    try:
        row = db.execute(
            "SELECT memory_type, facet_pending FROM episodes WHERE id = ?", (eid,)
        ).fetchone()
        assert row["facet_pending"] == 0
        assert row["memory_type"] in {"procedural", "priority", "semantic", "episodic"}
        vec_row = db.execute(
            "SELECT episode_id FROM vec_episodes WHERE episode_id = ?", (eid,)
        ).fetchone()
        assert vec_row is not None
    finally:
        db.close()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_worker_consumes_queue(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="hello",
                tool_calls=[],
                code_refs=[],
                token_count=1,
                ts=0,
            ),
        )
    finally:
        db.close()

    q: asyncio.Queue = asyncio.Queue()
    worker = FacetWorker(q)
    worker.start()
    await q.put(FacetEvent(project_id="p1", episode_id=eid, db_root=tmp_path))
    await q.join()
    await worker.stop()

    db = open_project_db(tmp_path, "p1")
    try:
        row = db.execute(
            "SELECT facet_pending FROM episodes WHERE id = ?", (eid,)
        ).fetchone()
        assert row["facet_pending"] == 0
    finally:
        db.close()
