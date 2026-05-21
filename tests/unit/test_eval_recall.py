import struct

import pytest

from spillover.archive.writer import Turn, archive_raw
from spillover.eval.dataset import EvalPair
from spillover.eval.recall_at_k import (
    RecallResult,
    recall_at_k,
    render_recall_report,
)
from spillover.storage.sqlite import open_project_db


def _seed_episode(tmp_path, content):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content=content,
                tool_calls=[],
                code_refs=[],
                token_count=10,
                ts=1,
            ),
        )
        # Insert vec row with placeholder zero embedding
        vec = [0.0] * 768
        db.execute(
            "INSERT INTO vec_episodes(episode_id, embedding, memory_type, importance, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, struct.pack("<768f", *vec), "episodic", 1.0, 1),
        )
    finally:
        db.close()
    return eid


def test_recall_at_k_counts_hits():
    results = [
        RecallResult(pair=EvalPair("q1", "ep1"), rank=1, top_ids=["ep1"]),
        RecallResult(pair=EvalPair("q2", "ep2"), rank=4, top_ids=["a", "b", "c", "ep2"]),
        RecallResult(pair=EvalPair("q3", "ep3"), rank=None, top_ids=["x", "y", "z"]),
    ]
    assert recall_at_k(results, 1) == pytest.approx(1 / 3)
    assert recall_at_k(results, 5) == pytest.approx(2 / 3)
    assert recall_at_k(results, 10) == pytest.approx(2 / 3)


def test_render_recall_report_includes_misses():
    results = [
        RecallResult(pair=EvalPair("q1", "ep1"), rank=None, top_ids=["x", "y"]),
    ]
    md = render_recall_report(results)
    assert "recall@5" in md
    assert "misses" in md.lower()
    assert "ep1" in md
