import logging

from spillover.logging import configure_root_logger, get_logger


def test_configure_idempotent():
    log1 = configure_root_logger()
    log2 = configure_root_logger()
    assert log1 is log2
    assert len(log1.handlers) == 1


def test_log_level_from_env(monkeypatch):
    monkeypatch.setenv("SPILLOVER_LOG_LEVEL", "DEBUG")
    # Clear any pre-existing config
    log = logging.getLogger("spillover")
    log.handlers.clear()
    log = configure_root_logger()
    assert log.level == logging.DEBUG


def test_get_logger_namespaced():
    log = get_logger("retriever")
    assert log.name == "spillover.retriever"
