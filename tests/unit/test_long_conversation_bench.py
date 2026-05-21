from spillover.bench.long_conversation import (
    LongConvResult,
    _check_anchors,
    _extract_text,
    all_scenarios,
    render_report,
)


def test_all_scenarios_have_anchors_and_question():
    sc = all_scenarios()
    assert len(sc) >= 2
    for s in sc:
        assert s.question
        assert s.expected_anchors
        assert len(s.pre_turns) >= 40  # at least 40 turns of background
        assert len(s.anchor_turns) >= 1
        assert len(s.filler_turns) >= 20  # at least 20 turns of post-anchor filler


def test_check_anchors_case_insensitive():
    hits, misses = _check_anchors("we picked SQLite for the local case", ["sqlite", "local", "postgres"])
    assert hits == ["sqlite", "local"]
    assert misses == ["postgres"]


def test_render_report_per_scenario_rows():
    results = [
        LongConvResult("sc1", "vanilla_truncated", "x", 100, 50, ["foo"], [], 100),
        LongConvResult("sc1", "spillover", "x", 200, 50, ["foo"], [], 200),
    ]
    md = render_report(results)
    assert "| sc1 | vanilla_truncated |" in md
    assert "| sc1 | spillover |" in md
