from spillover.budget.plan import plan_from_config
from spillover.config import Config


def test_plan_sums_to_ceiling_within_rounding(monkeypatch, tmp_path):
    monkeypatch.setenv("SPILLOVER_OPERATIONAL_CEILING_TOKENS", "500000")
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    plan = plan_from_config(Config.from_env())
    # Five int casts may round down -- allow up to 5 tokens drift
    assert plan.ceiling - plan.total <= 5
    assert plan.evictable_budget == plan.active_tokens


def test_plan_500k_default_split(monkeypatch, tmp_path):
    monkeypatch.setenv("SPILLOVER_OPERATIONAL_CEILING_TOKENS", "500000")
    monkeypatch.setenv("SPILLOVER_DB_ROOT", str(tmp_path))
    plan = plan_from_config(Config.from_env())
    assert plan.system_tokens == 20_000
    assert plan.working_memory_tokens == 100_000
    assert plan.active_tokens == 250_000
    assert plan.ltm_tokens == 75_000
    assert plan.scratchpad_tokens == 55_000
