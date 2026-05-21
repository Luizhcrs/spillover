from spillover.config import Config


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("SPILLOVER_PORT", raising=False)
    monkeypatch.delenv("SPILLOVER_WATERMARK", raising=False)
    monkeypatch.delenv("SPILLOVER_WINDOW_MAX", raising=False)
    monkeypatch.delenv("SPILLOVER_DB_ROOT", raising=False)
    monkeypatch.delenv("SPILLOVER_UPSTREAM_BASE_URL", raising=False)
    monkeypatch.delenv("SPILLOVER_OPENAI_BASE_URL", raising=False)
    cfg = Config.from_env()
    assert cfg.port == 8787
    assert cfg.watermark == 0.85
    assert cfg.window_max == 200_000
    assert cfg.upstream_base_url == "https://api.anthropic.com"
    assert cfg.openai_base_url == "https://api.openai.com"
    assert str(cfg.db_root).endswith(".spillover")
    assert cfg.ltm_budget_pct == 0.15
    assert cfg.retriever_topk == 8
    assert cfg.retriever_vector_k == 50
    assert cfg.retriever_graph_k == 50

def test_config_env_overrides(monkeypatch):
    monkeypatch.setenv("SPILLOVER_PORT", "9000")
    monkeypatch.setenv("SPILLOVER_WATERMARK", "0.9")
    monkeypatch.setenv("SPILLOVER_WINDOW_MAX", "1000000")
    monkeypatch.setenv("SPILLOVER_UPSTREAM_BASE_URL", "https://example.com")
    cfg = Config.from_env()
    assert cfg.port == 9000
    assert cfg.watermark == 0.9
    assert cfg.window_max == 1_000_000
    assert cfg.upstream_base_url == "https://example.com"
