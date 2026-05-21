from click.testing import CliRunner

from spillover.archive.writer import Turn, archive_raw
from spillover.cli import _resolve_pid, main
from spillover.storage.sqlite import open_project_db


def test_resolve_pid_passthrough_hex():
    assert _resolve_pid("abcdef12") == "abcdef12"


def test_resolve_pid_hashes_arbitrary():
    pid = _resolve_pid("my-cool-project")
    assert len(pid) == 40
    assert pid != "my-cool-project"


def test_stats_finds_db_when_raw_string_given(tmp_path, monkeypatch):
    """Reproduces the smoke bug: writing with sha1(raw), stats given raw must still find it."""
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    raw = "my-cool-project"
    hashed = _resolve_pid(raw)
    # Proxy writes under the hashed id (mirrors middleware behavior)
    db = open_project_db(tmp_path, hashed)
    try:
        archive_raw(
            db,
            Turn(
                project_id=hashed,
                role="user",
                content="hi",
                tool_calls=[],
                code_refs=[],
                token_count=1,
                ts=1,
            ),
        )
    finally:
        db.close()

    runner = CliRunner()
    # User passes the raw string — stats should find the db
    result = runner.invoke(main, ["stats", raw])
    assert result.exit_code == 0
    assert "episodes: 1" in result.output
