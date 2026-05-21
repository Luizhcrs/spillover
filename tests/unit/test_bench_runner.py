from spillover.bench.runner import (
    TaskResult,
    _check_anchors,
    _extract_text,
    render_ab_report,
)


def test_check_anchors_case_insensitive():
    hits, misses = _check_anchors("the SQLite db is local", ["sqlite", "local", "postgres"])
    assert "sqlite" in hits
    assert "local" in hits
    assert "postgres" in misses


def test_extract_text_anthropic_shape():
    content = [{"type": "text", "text": "hello"}, {"type": "text", "text": " world"}]
    text = _extract_text({"content": content})
    assert text == "hello world"


def test_render_ab_report_has_per_task_rows():
    results = [
        TaskResult(
            task_id="t1", mode="vanilla", response_text="ok",
            input_tokens=10, output_tokens=5,
            anchors_hit=["foo"], anchors_missed=[]
        ),
        TaskResult(
            task_id="t1", mode="spillover", response_text="ok",
            input_tokens=12, output_tokens=5,
            anchors_hit=["foo"], anchors_missed=[]
        ),
    ]
    md = render_ab_report(results)
    assert "| t1 | vanilla |" in md
    assert "| t1 | spillover |" in md
    assert "tasks with all anchors hit" in md
