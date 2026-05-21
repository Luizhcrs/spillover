import struct

from spillover.retriever.vector import vector_topk
from spillover.storage.sqlite import open_project_db


def _b(v):
    return struct.pack(f"<{len(v)}f", *v)


def test_vector_topk_orders_by_distance(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        for eid, vec in [
            ("e1", [1.0] + [0.0] * 767),
            ("e2", [0.9] + [0.0] * 767),
            ("e3", [0.0, 1.0] + [0.0] * 766),
            ("e4", [-1.0] + [0.0] * 767),
        ]:
            db.execute(
                "INSERT INTO vec_episodes(episode_id, embedding, memory_type, "
                "importance, ts) VALUES (?, ?, ?, ?, ?)",
                (eid, _b(vec), "episodic", 1.0, 0),
            )
        hits = vector_topk(db, [1.0] + [0.0] * 767, k=3)
        ids = [h.episode_id for h in hits]
        assert ids[0] == "e1"
        assert "e4" not in ids
        assert len(hits) == 3
        assert all(h.source == "vector" for h in hits)
    finally:
        db.close()
