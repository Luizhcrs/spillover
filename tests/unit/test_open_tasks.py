from spillover.facet.classifier import classify
from spillover.facet.tasks import has_open_task


def test_detects_todo_marker():
    assert has_open_task("TODO: implement the BM25 leg")


def test_detects_fixme_marker():
    assert has_open_task("FIXME this regex is fragile")


def test_detects_pending_english():
    assert has_open_task("we still need to write the chaos test")


def test_detects_ptbr_pending():
    assert has_open_task("ainda preciso integrar o decay scheduler")
    assert has_open_task("faltam fazer os testes de integração")


def test_no_open_task_in_done_work():
    assert not has_open_task("implemented the BM25 leg and committed.")


def test_classifier_picks_task_over_priority():
    """Open-task wins over priority — we want pending items to surface even
    when described in important-sounding language."""
    result = classify("TODO: this is important — fix the auth bug")
    assert result == "task"


def test_classifier_falls_through_to_episodic_without_task():
    result = classify("ran the tests they passed")
    assert result == "episodic"
