from spillover.counter_compact.env_vars import env_for


def test_cc_disable_vars():
    e = env_for("cc")
    assert e["CLAUDE_CODE_AUTO_COMPACT"] == "0"
    assert e["CLAUDE_CODE_DISABLE_COMPACT"] == "1"


def test_codex_disable_vars():
    e = env_for("codex")
    assert e["CODEX_DISABLE_COMPACT"] == "1"


def test_unknown_cli_returns_empty():
    assert env_for("nonexistent") == {}
