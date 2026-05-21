import time

from spillover.archive.writer import Turn, archive_raw
from spillover.retriever.render import render_ltm_block
from spillover.retriever.vector import Hit
from spillover.storage.sqlite import open_project_db


def test_render_empty_returns_empty():
    class _Stub:
        def execute(self, *args, **kwargs):
            raise AssertionError("should not be called")

    assert render_ltm_block(_Stub(), []) == ""


def test_render_wraps_in_block(tmp_path):
    db = open_project_db(tmp_path, "p1")
    try:
        eid = archive_raw(
            db,
            Turn(
                project_id="p1",
                role="user",
                content="hello world",
                tool_calls=[],
                code_refs=[],
                token_count=2,
                ts=int(time.time() * 1000),
            ),
        )
        db.execute("UPDATE episodes SET memory_type='episodic' WHERE id=?", (eid,))
        out = render_ltm_block(db, [Hit(eid, 1.0)])
        assert out.startswith("<spillover-ltm>")
        assert out.endswith("</spillover-ltm>")
        assert "Below are excerpts of YOUR OWN past statements" in out
        assert "hello world" in out
        assert f'id="{eid}"' in out
    finally:
        db.close()
