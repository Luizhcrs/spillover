from spillover.bench.ab import (
    RunResult,
    detect_regressions_for_response,
    render_markdown,
    summarize_runs,
)


def test_detect_regressions_en():
    markers = detect_regressions_for_response("Sorry, I don't remember that earlier message.")
    assert markers


def test_detect_regressions_none():
    markers = detect_regressions_for_response("Sure, here is the code.")
    assert markers == []


def test_summarize_runs_aggregates():
    runs = [
        RunResult(
            task_id="t1", used_spillover=False, response="ok",
            input_tokens=100, output_tokens=20,
        ),
        RunResult(
            task_id="t2", used_spillover=False, response="forgot",
            input_tokens=200, output_tokens=10,
            regression_markers=["forgot"],
        ),
        RunResult(
            task_id="t1", used_spillover=True, response="ok",
            input_tokens=110, output_tokens=20,
        ),
    ]
    s = summarize_runs(runs)
    assert s["vanilla"]["tasks"] == 2
    assert s["spillover"]["tasks"] == 1
    assert s["vanilla"]["regression_markers"] == 1
    assert s["spillover"]["regression_markers"] == 0


def test_render_markdown_includes_both_columns():
    runs = [
        RunResult(
            task_id="t1", used_spillover=False, response="x",
            input_tokens=10, output_tokens=5,
        ),
        RunResult(
            task_id="t1", used_spillover=True, response="x",
            input_tokens=12, output_tokens=5,
        ),
    ]
    md = render_markdown(summarize_runs(runs))
    assert "| vanilla |" in md
    assert "| spillover |" in md
