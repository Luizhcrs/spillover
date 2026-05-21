import time

from spillover.archive.writer import Turn, archive_raw
from spillover.retriever.budget import trim_to_budget
from spillover.retriever.vector import Hit
from spillover.storage.sqlite import open_project_db


def test_trim_to_budget_stops_at_cap(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        ids = []
        for i in range(5):
            ids.append(
                archive_raw(
                    db,
                    Turn(
                        project_id="p1",
                        role="user",
                        content="x" * 200,
                        tool_calls=[],
                        code_refs=[],
                        token_count=50,
                        ts=int(time.time() * 1000) + i,
                    ),
                )
            )
        hits = [Hit(eid, 1.0) for eid in ids]
        kept = trim_to_budget(db, hits, max_tokens=120)
        assert len(kept) == 2
    finally:
        db.close()


def test_trim_zero_budget_returns_empty(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        assert trim_to_budget(db, [Hit("x", 1.0)], max_tokens=0) == []
    finally:
        db.close()
