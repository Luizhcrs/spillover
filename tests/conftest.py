import pytest

from spillover.config import Config


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    monkeypatch.setenv("SPILLOVER_WINDOW_MAX", "1000")
    monkeypatch.setenv("SPILLOVER_WATERMARK", "0.85")
    return Config.from_env()
