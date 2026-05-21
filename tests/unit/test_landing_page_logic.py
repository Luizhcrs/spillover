from spillover.bench.landing_page_scenario import (
    LANDING_PAGE_DETAILS,
    LogicResult,
    _check_any,
    build_landing_page_history,
    render_logic_report,
)


def test_history_has_all_details_present():
    history = build_landing_page_history()
    full_text = " ".join(t["content"] for t in history)
    assert "#06FFB0" in full_text
    assert "Inter" in full_text
    assert "Geist" in full_text
    assert "Stop compacting" in full_text
    assert "work@yourcompany.com" in full_text
    assert "2026" in full_text


def test_history_pushes_decisions_into_evicted_region():
    history = build_landing_page_history()
    assert len(history) >= 40
    # Last 8 turns shouldn't contain the early decisions (proves they'd be lost
    # under vanilla truncation)
    tail_text = " ".join(t["content"] for t in history[-8:])
    assert "06FFB0" not in tail_text
    assert "Stop compacting" not in tail_text


def test_check_any_case_insensitive():
    hits, missed = _check_any("we use Inter for body", ["Inter", "Geist"])
    assert "Inter" in hits
    assert missed is False
    hits, missed = _check_any("hi", ["Inter"])
    assert hits == []
    assert missed is True


def test_render_logic_report_renders_per_mode_count():
    results = [
        LogicResult("d1", "vanilla_truncated", "ok", [], True, 10, 5, 100),
        LogicResult("d1", "spillover", "ok #06FFB0", ["#06FFB0"], False, 200, 5, 200),
    ]
    md = render_logic_report(results)
    assert "vanilla_truncated**: 0/1" in md
    assert "spillover**: 1/1" in md


def test_all_details_have_clear_expected():
    for d in LANDING_PAGE_DETAILS:
        assert d.expected
        assert all(isinstance(e, str) for e in d.expected)
