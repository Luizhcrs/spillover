import sys
from unittest.mock import patch

from click.testing import CliRunner

from spillover.wrappers.cc import main


def test_wrapper_passes_proxy_url(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_PORT", "9999")
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    runner = CliRunner()

    captured = {}

    def _fake_run(cmd, env, check):
        captured["env"] = env
        captured["cmd"] = cmd

        class _R:
            returncode = 0

        return _R()

    with patch("spillover.wrappers.cc.subprocess.run", side_effect=_fake_run):
        with patch.object(sys, "exit") as _mock_exit:
            result = runner.invoke(main, ["--project", "proj-test", "--help-claude-code"])

    assert "ANTHROPIC_BASE_URL" in captured["env"]
    assert captured["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999"
    assert captured["env"]["CLAUDE_CODE_AUTO_COMPACT"] == "0"
    assert captured["env"]["SPILLOVER_PROJECT_ID"] == "proj-test"
    assert result.exit_code == 0


def test_wrapper_default_project_is_cwd_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    runner = CliRunner()

    captured = {}

    def _fake_run(cmd, env, check):
        captured["env"] = env

        class _R:
            returncode = 0

        return _R()

    with patch("spillover.wrappers.cc.subprocess.run", side_effect=_fake_run):
        with patch.object(sys, "exit"):
            runner.invoke(main)

    assert "SPILLOVER_PROJECT_ID" in captured["env"]
    pid = captured["env"]["SPILLOVER_PROJECT_ID"]
    assert len(pid) == 40  # sha1 hex
