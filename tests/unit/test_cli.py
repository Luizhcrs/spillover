import time

from click.testing import CliRunner

from spillover.archive.writer import Turn, archive_raw
from spillover.cli import main
from spillover.storage.sqlite import open_project_db


def test_stats_empty_project(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(main, ["stats", "nonexistent"])
    assert result.exit_code == 0
    assert "episodes: 0" in result.output


def test_stats_with_episodes(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    db = open_project_db(tmp_path, "p1")
    try:
        for i in range(3):
            archive_raw(
                db,
                Turn(
                    project_id="p1",
                    role="user",
                    content=f"msg {i}",
                    tool_calls=[],
                    code_refs=[],
                    token_count=10,
                    ts=int(time.time() * 1000) + i,
                ),
            )
        db.execute("UPDATE episodes SET evicted=1")
    finally:
        db.close()

    runner = CliRunner()
    result = runner.invoke(main, ["stats", "p1"])
    assert result.exit_code == 0
    assert "episodes: 3" in result.output
    assert "evicted: 3" in result.output


def test_up_shows_help_for_now(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(main, ["up", "--help"])
    assert result.exit_code == 0
    assert "Start the spillover proxy" in result.output
