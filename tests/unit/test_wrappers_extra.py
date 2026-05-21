import sys
from unittest.mock import patch

from click.testing import CliRunner

from spillover.wrappers.codex import main as codex_main
from spillover.wrappers.cursor import main as cursor_main
from spillover.wrappers.continue_dev import main as continue_main


def _run(main, module_name: str, monkeypatch, tmp_path):
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

    with patch(f"{module_name}.subprocess.run", side_effect=_fake_run):
        with patch.object(sys, "exit"):
            runner.invoke(main, ["--project", "p-test"])
    return captured


def test_codex_wrapper_env(monkeypatch, tmp_path):
    cap = _run(codex_main, "spillover.wrappers.codex", monkeypatch, tmp_path)
    assert cap["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999"
    assert cap["env"]["OPENAI_BASE_URL"] == "http://127.0.0.1:9999"
    assert cap["env"]["SPILLOVER_PROJECT_ID"] == "p-test"


def test_cursor_wrapper_env(monkeypatch, tmp_path):
    cap = _run(cursor_main, "spillover.wrappers.cursor", monkeypatch, tmp_path)
    assert "ANTHROPIC_BASE_URL" in cap["env"]
    assert cap["env"]["SPILLOVER_PROJECT_ID"] == "p-test"


def test_continue_wrapper_env(monkeypatch, tmp_path):
    cap = _run(continue_main, "spillover.wrappers.continue_dev", monkeypatch, tmp_path)
    assert "ANTHROPIC_BASE_URL" in cap["env"]
    assert cap["env"]["SPILLOVER_PROJECT_ID"] == "p-test"
